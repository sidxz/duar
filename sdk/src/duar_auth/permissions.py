"""Async HTTP client for checking permissions against the identity service."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass

import httpx

from duar_auth._utils import warn_if_insecure
from duar_auth.types import DuarError


class _TTLCache:
    """Minimal TTL cache using stdlib only.  Thread-safe enough for async
    single-threaded event loops (no locks needed).

    Entries are evicted lazily on read. A periodic ``_evict()`` runs when
    the cache exceeds ``maxsize * 1.5``: expired entries go first, then the
    oldest insertions until back at ``maxsize`` — so a high-cardinality burst
    within one TTL window can't grow the cache unbounded.
    """

    __slots__ = ("_store", "_ttl", "_maxsize")

    def __init__(self, ttl: float, maxsize: int = 2048):
        self._store: dict[tuple, tuple[float, object]] = {}
        self._ttl = ttl
        self._maxsize = maxsize

    def get(self, key: tuple) -> object | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: tuple, value: object) -> None:
        self._store[key] = (time.monotonic(), value)
        if len(self._store) > int(self._maxsize * 1.5):
            self._evict()

    def invalidate(self, *key_prefixes: str) -> None:
        """Remove all entries whose key starts with any of the given prefixes."""
        if not key_prefixes:
            self._store.clear()
            return
        to_remove = [k for k in self._store if k[0] in key_prefixes]
        for k in to_remove:
            del self._store[k]

    def clear(self) -> None:
        self._store.clear()

    def _evict(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]
        # ponytail: oldest-insertion drop, not LRU — read-recency tracking isn't
        # worth it for a cache whose entries live one short TTL anyway.
        overflow = len(self._store) - self._maxsize
        if overflow > 0:
            oldest = sorted(self._store, key=lambda k: self._store[k][0])[:overflow]
            for k in oldest:
                del self._store[k]

    def __len__(self) -> int:
        return len(self._store)


@dataclass
class PermissionCheck:
    service_name: str
    resource_type: str
    resource_id: uuid.UUID
    action: str  # 'view' | 'edit'


@dataclass
class PermissionResult:
    service_name: str
    resource_type: str
    resource_id: uuid.UUID
    action: str
    allowed: bool


class PermissionClient:
    """Client for the identity service's permission API.

    Args:
        base_url: Root URL of the Duar service.
        service_name: Registered service name.
        service_key: Service API key.
        cache_ttl: Seconds to cache ``accessible()`` and ``can()`` results.
            ``0`` (default) disables caching.  Recommended: ``30``–``60`` for
            apps where permission changes are infrequent.
    """

    def __init__(
        self,
        base_url: str,
        service_name: str,
        service_key: str | None = None,
        cache_ttl: float = 0,
    ):
        self.base_url = base_url.rstrip("/")
        self.service_name = service_name
        self.service_key = service_key
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=5.0)
        self._cache: _TTLCache | None = _TTLCache(ttl=cache_ttl) if cache_ttl > 0 else None
        warn_if_insecure(self.base_url, "PermissionClient")

    def __repr__(self) -> str:
        return f"PermissionClient(base_url={self.base_url!r}, service_name={self.service_name!r})"

    @staticmethod
    def _token_key(token: str) -> str:
        """Truncated hash of the token for use as a cache key component."""
        return hashlib.sha256(token.encode()).hexdigest()[:16]

    def _headers(self, token: str | None = None) -> dict[str, str]:
        """Build request headers with service key and optional user JWT."""
        h: dict[str, str] = {}
        if self.service_key:
            h["X-Service-Key"] = self.service_key
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    @staticmethod
    def _check(response: httpx.Response) -> None:
        """Raise DuarError on non-2xx responses."""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DuarError(
                f"Duar API error: {exc.response.status_code}",
                status_code=exc.response.status_code,
            ) from exc

    async def check(
        self,
        token: str,
        checks: list[PermissionCheck],
    ) -> list[PermissionResult]:
        """Batch check permissions. Pass the user's JWT as the token."""
        response = await self._client.post(
            "/permissions/check",
            headers=self._headers(token),
            json={
                "checks": [
                    {
                        "service_name": c.service_name,
                        "resource_type": c.resource_type,
                        "resource_id": str(c.resource_id),
                        "action": c.action,
                    }
                    for c in checks
                ]
            },
        )
        self._check(response)
        data = response.json()
        return [
            PermissionResult(
                service_name=r["service_name"],
                resource_type=r["resource_type"],
                resource_id=uuid.UUID(r["resource_id"]),
                action=r["action"],
                allowed=r["allowed"],
            )
            for r in data["results"]
        ]

    async def can(
        self,
        token: str,
        resource_type: str,
        resource_id: uuid.UUID,
        action: str,
    ) -> bool:
        """Convenience: check a single permission.

        Results are cached when ``cache_ttl > 0``.
        """
        if self._cache is not None:
            key = ("can", self._token_key(token), resource_type, str(resource_id), action)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        results = await self.check(
            token,
            [PermissionCheck(self.service_name, resource_type, resource_id, action)],
        )
        allowed = results[0].allowed if results else False
        if self._cache is not None:
            self._cache.set(key, allowed)
        return allowed

    def _invalidate_cache(self) -> None:
        """Clear cached ``accessible`` and ``can`` results after a write operation."""
        if self._cache is not None:
            self._cache.invalidate("accessible", "can")

    async def register_resource(
        self,
        resource_type: str,
        resource_id: uuid.UUID,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        visibility: str = "workspace",
    ) -> dict:
        """Register a new resource (service-key only, no user JWT needed)."""
        response = await self._client.post(
            "/permissions/register",
            headers=self._headers(),
            json={
                "service_name": self.service_name,
                "resource_type": resource_type,
                "resource_id": str(resource_id),
                "workspace_id": str(workspace_id),
                "owner_id": str(owner_id),
                "visibility": visibility,
            },
        )
        self._check(response)
        self._invalidate_cache()
        return response.json()

    async def deregister_resource(
        self,
        resource_type: str,
        resource_id: uuid.UUID,
    ) -> None:
        """Delete a resource permission and all its shares (service-key only)."""
        response = await self._client.delete(
            f"/permissions/resource/{self.service_name}/{resource_type}/{resource_id}",
            headers=self._headers(),
        )
        self._check(response)
        self._invalidate_cache()

    async def share(
        self,
        token: str,
        resource_type: str,
        resource_id: uuid.UUID,
        grantee_type: str,
        grantee_id: uuid.UUID,
        permission: str = "view",
    ) -> dict:
        """Share a resource with a user or group.

        Looks up the permission record by resource coordinates, then shares.
        """
        # Resolve resource coordinates → permission_id
        lookup = await self._client.get(
            f"/permissions/resource/{self.service_name}/{resource_type}/{resource_id}",
            headers=self._headers(),
        )
        self._check(lookup)
        permission_id = lookup.json()["id"]

        # Share
        response = await self._client.post(
            f"/permissions/{permission_id}/share",
            headers=self._headers(token),
            json={
                "grantee_type": grantee_type,
                "grantee_id": str(grantee_id),
                "permission": permission,
            },
        )
        self._check(response)
        self._invalidate_cache()
        return response.json()

    async def accessible(
        self,
        token: str,
        resource_type: str,
        action: str,
        workspace_id: uuid.UUID,
        limit: int | None = None,
    ) -> tuple[list[uuid.UUID], bool]:
        """Lookup accessible resource IDs for the current user.

        Returns (resource_ids, has_full_access). When has_full_access is True
        and no limit was set, resource_ids is empty — the caller should skip
        filtering entirely.

        Results are cached when ``cache_ttl > 0``.
        """
        if self._cache is not None:
            key = ("accessible", self._token_key(token), resource_type, action, str(workspace_id), limit)
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        payload: dict = {
            "service_name": self.service_name,
            "resource_type": resource_type,
            "action": action,
            "workspace_id": str(workspace_id),
        }
        if limit is not None:
            payload["limit"] = limit
        response = await self._client.post(
            "/permissions/accessible",
            headers=self._headers(token),
            json=payload,
        )
        self._check(response)
        data = response.json()
        result = (
            [uuid.UUID(rid) for rid in data["resource_ids"]],
            data["has_full_access"],
        )
        if self._cache is not None:
            self._cache.set(key, result)
        return result

    async def unshare(
        self,
        token: str,
        resource_type: str,
        resource_id: uuid.UUID,
        grantee_type: str,
        grantee_id: uuid.UUID,
        permission: str = "view",
    ) -> dict:
        """Revoke a share on a resource.

        Looks up the permission record by resource coordinates, then revokes.
        """
        lookup = await self._client.get(
            f"/permissions/resource/{self.service_name}/{resource_type}/{resource_id}",
            headers=self._headers(),
        )
        self._check(lookup)
        permission_id = lookup.json()["id"]

        response = await self._client.request(
            "DELETE",
            f"/permissions/{permission_id}/share",
            headers=self._headers(token),
            json={
                "grantee_type": grantee_type,
                "grantee_id": str(grantee_id),
                "permission": permission,
            },
        )
        self._check(response)
        self._invalidate_cache()
        return response.json()

    async def update_visibility(
        self,
        token: str,
        resource_type: str,
        resource_id: uuid.UUID,
        visibility: str,
    ) -> dict:
        """Update resource visibility (private/workspace).

        Looks up the permission record by resource coordinates, then patches.
        """
        lookup = await self._client.get(
            f"/permissions/resource/{self.service_name}/{resource_type}/{resource_id}",
            headers=self._headers(),
        )
        self._check(lookup)
        permission_id = lookup.json()["id"]

        response = await self._client.patch(
            f"/permissions/{permission_id}/visibility",
            headers=self._headers(token),
            json={"visibility": visibility},
        )
        self._check(response)
        self._invalidate_cache()
        return response.json()

    async def get_resource_acl(
        self,
        resource_type: str,
        resource_id: uuid.UUID,
    ) -> dict:
        """Get the full ACL record for a resource, including shares."""
        response = await self._client.get(
            f"/permissions/resource/{self.service_name}/{resource_type}/{resource_id}",
            headers=self._headers(),
        )
        self._check(response)
        return response.json()

    async def get_enriched_resource_acl(
        self,
        resource_type: str,
        resource_id: uuid.UUID,
    ) -> dict:
        """Get the ACL with user profiles resolved inline (names, emails)."""
        response = await self._client.get(
            f"/permissions/resource/{self.service_name}/{resource_type}/{resource_id}/enriched",
            headers=self._headers(),
        )
        self._check(response)
        return response.json()

    async def search_workspace_members(
        self,
        token: str,
        workspace_id: uuid.UUID,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Search workspace members by name or email."""
        params: dict[str, str] = {}
        if query:
            params["q"] = query
        if limit is not None:
            params["limit"] = str(limit)
        response = await self._client.get(
            f"/workspaces/{workspace_id}/members",
            headers=self._headers(token),
            params=params,
        )
        self._check(response)
        return response.json()

    async def list_groups(
        self,
        token: str,
        workspace_id: uuid.UUID,
    ) -> list[dict]:
        """List groups in a workspace."""
        response = await self._client.get(
            f"/workspaces/{workspace_id}/groups",
            headers=self._headers(token),
        )
        self._check(response)
        return response.json()

    async def get_group_members(
        self,
        token: str,
        workspace_id: uuid.UUID,
        group_id: uuid.UUID,
    ) -> list[dict]:
        """List members of a group."""
        response = await self._client.get(
            f"/workspaces/{workspace_id}/groups/{group_id}/members",
            headers=self._headers(token),
        )
        self._check(response)
        return response.json()

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
