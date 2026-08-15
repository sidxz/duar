"""Duar autoconfig — single entry point for integrating FastAPI apps."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from jwt.algorithms import RSAAlgorithm

from duar_auth._utils import warn_if_insecure
from duar_auth.auth import SystemAuth
from duar_auth.authz import AuthzClient
from duar_auth.authz_middleware import AuthzMiddleware
from duar_auth.dependencies import get_current_user, get_request_auth_factory
from duar_auth.dependencies import require_action as _require_action
from duar_auth.middleware import JWTAuthMiddleware
from duar_auth.permissions import PermissionClient
from duar_auth.roles import RoleClient
from duar_auth.types import DuarError

_AUD_M2M = "duar:m2m"


class Duar:
    """One-line integration with the Duar identity service.

    Operates in two modes:

    **AuthZ mode** (default): Client apps authenticate users directly with
    their IdP. Duar validates the IdP token and issues an authorization-only
    JWT. The SDK middleware validates both tokens on each request.

    **Proxy mode**: Duar handles the entire OAuth flow and issues a single
    JWT containing both identity and authorization claims.

    Args:
        base_url: Root URL of the Duar identity service.
        service_name: The service name registered in Duar.
        service_key: Service API key (from admin panel).
        mode: ``"authz"`` (default) or ``"proxy"``.
        idp_public_key: PEM-encoded public key for validating IdP tokens.
            One of ``idp_public_key`` or ``idp_jwks_url`` is required when
            ``mode="authz"``.
        idp_jwks_url: JWKS endpoint URL for IdP token validation (e.g.
            ``https://www.googleapis.com/oauth2/v3/certs``).  Preferred
            over ``idp_public_key`` as it handles key rotation automatically.
        actions: Optional list of RBAC action dicts to register on startup.
        allowed_workspaces: Optional set of workspace IDs permitted to access
            this service. ``None`` allows all. Only used in proxy mode.
        cache_ttl: Seconds to cache ``accessible()`` and ``can()`` results
            in the ``PermissionClient``.  ``0`` (default) disables caching.
            Recommended: ``30``–``60`` for apps where permission changes are
            infrequent.  Write operations (share, unshare, visibility changes)
            automatically invalidate the cache.
    """

    def __init__(
        self,
        base_url: str,
        service_name: str,
        service_key: str,
        mode: str = "authz",
        idp_public_key: str | None = None,
        idp_jwks_url: str | None = None,
        idp_audience: str | list[str] | None = None,
        idp_issuer: str | None = None,
        actions: list[dict] | None = None,
        allowed_workspaces: set[str] | None = None,
        cache_ttl: float = 0,
    ):
        if not service_key:
            raise ValueError(
                "service_key is required. Create a service app in the Duar "
                "admin panel (/admin/service-apps) and pass the key here."
            )
        if mode not in ("authz", "proxy"):
            raise ValueError(f"mode must be 'authz' or 'proxy', got '{mode}'")
        if mode == "authz":
            if not idp_public_key and not idp_jwks_url:
                raise ValueError("idp_public_key or idp_jwks_url is required when mode='authz'")
            if not idp_audience:
                raise ValueError(
                    "idp_audience is required when mode='authz' — the IdP token's "
                    "aud claim must be verified to prevent accepting tokens minted "
                    "for other OAuth clients of the same IdP."
                )

        self.base_url = base_url.rstrip("/")
        warn_if_insecure(self.base_url, "Duar")
        self.service_name = service_name
        self.service_key = service_key
        self.mode = mode
        self.idp_public_key = idp_public_key
        self.idp_jwks_url = idp_jwks_url
        self.idp_audience = idp_audience
        self.idp_issuer = idp_issuer
        self.actions = actions
        self.allowed_workspaces = allowed_workspaces
        self.cache_ttl = cache_ttl

        self._permissions: PermissionClient | None = None
        self._roles: RoleClient | None = None
        self._authz: AuthzClient | None = None
        self._duar_public_key: str | None = None
        self._effective_scope: str | None = None
        self._realm: dict | None = None
        self._m2m_token: str | None = None
        self._m2m_refresh_at: float = 0.0

    def __repr__(self) -> str:
        return f"Duar(base_url={self.base_url!r}, service_name={self.service_name!r})"

    @property
    def duar_public_key(self) -> str | None:
        """Duar's public key, fetched during lifespan startup."""
        return self._duar_public_key

    @property
    def effective_scope(self) -> str:
        """Shared scope this service reads/writes under: the realm slug when a
        member (discovered via ``fetch_whoami``), else the service's own name."""
        return self._effective_scope or self.service_name

    @property
    def realm(self) -> dict | None:
        """The realm this service belongs to (``{slug, name}``), or ``None`` if standalone."""
        return self._realm

    # -- Lazy clients --------------------------------------------------------

    @property
    def permissions(self) -> PermissionClient:
        """Lazily-created permission client."""
        if self._permissions is None:
            self._permissions = PermissionClient(
                base_url=self.base_url,
                service_name=self.effective_scope,
                service_key=self.service_key,
                cache_ttl=self.cache_ttl,
            )
        return self._permissions

    @property
    def roles(self) -> RoleClient:
        """Lazily-created role client."""
        if self._roles is None:
            self._roles = RoleClient(
                base_url=self.base_url,
                service_name=self.effective_scope,
                service_key=self.service_key,
            )
        return self._roles

    @property
    def authz(self) -> AuthzClient:
        """Lazily-created authz client."""
        if self._authz is None:
            self._authz = AuthzClient(self.base_url, self.service_key)
        return self._authz

    def proxy_router(self, timeout: float = 10.0):
        """Reverse-proxy router for private-network Duar deployments.

        Forwards the browser-facing Duar surface (discovery/mint via
        ``/authz/resolve`` with the service key injected, plus the read-only
        workspace-directory endpoints with the caller's tokens passed through)
        so the frontend can use a same-origin ``duarUrl``. See
        :mod:`duar_auth.proxy`.

        Usage: ``app.include_router(duar.proxy_router(), prefix="/api/duar")``
        """
        from duar_auth.proxy import create_proxy_router

        return create_proxy_router(self.base_url, self.service_key, timeout=timeout)

    # -- Middleware -----------------------------------------------------------

    def protect(
        self,
        app: FastAPI,
        exclude_paths: list[str] | None = None,
    ) -> None:
        """Add authentication middleware to the app.

        In authz mode: adds ``AuthzMiddleware`` (validates IdP + authz tokens).
        In proxy mode: adds ``JWTAuthMiddleware`` (validates Duar JWT).

        In authz mode, the middleware reads keys lazily from this ``Duar``
        instance, so ``protect()`` can safely be called at module level before
        the lifespan fetches Duar's public key.
        """
        if self.mode == "authz":
            app.add_middleware(
                AuthzMiddleware,
                duar_instance=self,
                service_name=self.service_name,
                idp_audience=self.idp_audience,
                idp_issuer=self.idp_issuer,
                exclude_paths=exclude_paths,
            )
        else:
            app.add_middleware(
                JWTAuthMiddleware,
                base_url=self.base_url,
                exclude_paths=exclude_paths,
                allowed_workspaces=self.allowed_workspaces,
            )

    async def fetch_duar_public_key(self) -> str:
        """Fetch Duar's public key from its JWKS endpoint."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/.well-known/jwks.json")
            resp.raise_for_status()
            jwks = resp.json()
        if not jwks.get("keys"):
            raise RuntimeError("No keys found in Duar JWKS response")
        key_data = jwks["keys"][0]
        pub_key = RSAAlgorithm.from_jwk(key_data)
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        self._duar_public_key = pub_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
        return self._duar_public_key

    async def fetch_whoami(self) -> dict | None:
        """Self-discover the shared realm scope from Duar — no app-side config.

        Sets ``effective_scope`` to the realm slug when this service is a realm
        member, else leaves it as ``service_name``. Tolerant of a pre-realm Duar
        (``/realm`` absent → 404) or an unreachable internal listener: returns
        ``None`` and stays standalone, so older/partial deployments keep working.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/realm/whoami",
                    headers={"X-Service-Key": self.service_key},
                )
            if resp.status_code != 200:
                return None
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            # ValueError = a non-JSON 200 (e.g. an ingress that serves the admin
            # SPA/HTML for /realm) — degrade to standalone, never crash startup.
            return None
        self._effective_scope = data.get("effective_scope")
        self._realm = data.get("realm")
        # Realm member: re-point any already-created permission/role clients at the
        # shared scope. The get_auth dependency factory captures these instances by
        # reference, so mutating .service_name in place updates that path too.
        if self._effective_scope:
            if self._permissions is not None:
                self._permissions.service_name = self._effective_scope
            if self._roles is not None:
                self._roles.service_name = self._effective_scope
        return data

    # -- Lifespan ------------------------------------------------------------

    @property
    def lifespan(self) -> Callable[[FastAPI], AsyncIterator[None]]:
        """Return an async context manager factory for ``FastAPI(lifespan=...)``.

        On startup:
        - In authz mode: fetches Duar's public key from JWKS endpoint.
        - Registers RBAC actions (if any were provided).
        On shutdown: closes HTTP clients.
        """

        @asynccontextmanager
        async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
            if self.mode == "authz":
                await self.fetch_duar_public_key()
            await self.fetch_whoami()
            if self.actions:
                await self.roles.register_actions(self.actions)
            yield
            if self._permissions is not None:
                await self._permissions.close()
            if self._roles is not None:
                await self._roles.close()
            if self._authz is not None:
                await self._authz.close()

        return _lifespan

    # -- Dependency helpers --------------------------------------------------

    @property
    def require_user(self) -> Callable:
        """FastAPI dependency returning the authenticated user."""
        return get_current_user

    @property
    def get_auth(self) -> Callable:
        """FastAPI dependency returning a ``RequestAuth`` for the current request."""
        return get_request_auth_factory(
            permissions=self.permissions,
            roles=self.roles,
        )

    def require_action(self, action: str) -> Callable:
        """Dependency factory that enforces an RBAC action."""
        return _require_action(self.roles, action)

    # -- No-user (m2m) tokens -------------------------------------------------

    def verify_m2m_token(self, token: str) -> SystemAuth:
        """Verify an inbound no-user realm token and return its ``SystemAuth``.

        Receiver side of Flow B: App B calls this on a token App A minted via
        ``mint_m2m_token``. Trust is rooted entirely in Duar's RS256 signature
        (only Duar holds the private key) plus aud/type/svc binding — never
        app↔app trust. The token's ``svc`` must equal this service's
        ``effective_scope``, so a token minted for another realm cannot be replayed.

        Mount the protected route OUTSIDE ``AuthzMiddleware`` (add it to
        ``exclude_paths``): an m2m call carries no IdP token, so the dual-token
        middleware would 401 it. Gate it with ``require_system`` instead.

        Raises ``DuarError`` (``status_code`` 401 for bad/expired/wrong-type,
        403 for wrong realm / wrong target).
        """
        key = self._duar_public_key
        if not key:
            raise DuarError(
                "Duar public key not available; run the app under duar.lifespan so it is fetched at startup.",
                503,
            )
        try:
            # ponytail: verifies against the single PEM fetched once at lifespan —
            # not kid-rotation-aware mid-process like AuthzMiddleware's PyJWKClient.
            # m2m TTL is short (default 300s) and a restart refetches; upgrade to a
            # PyJWKClient against {base_url}/.well-known/jwks.json if mid-process key
            # rotation must not interrupt m2m acceptance.
            payload = jwt.decode(token, key, algorithms=["RS256"], audience=_AUD_M2M)
        except jwt.ExpiredSignatureError as exc:
            raise DuarError("m2m token expired", 401) from exc
        except jwt.InvalidTokenError as exc:
            raise DuarError("Invalid m2m token", 401) from exc
        if payload.get("type") != "m2m":
            raise DuarError("Not an m2m token", 401)
        if payload.get("svc") != self.effective_scope:
            raise DuarError("m2m token was issued for a different realm", 403)
        aud_target = payload.get("aud_target")
        if aud_target is not None and aud_target != self.service_name:
            raise DuarError("m2m token targets a different service", 403)
        return SystemAuth(
            caller=payload.get("caller", ""),
            actions=list(payload.get("actions") or []),
            svc=payload["svc"],
        )

    async def mint_m2m_token(self) -> str:
        """Mint (or return a cached) no-user realm m2m token for an outbound call.

        Sender side of Flow B: App A calls this, then forwards the token in
        ``Authorization: Bearer`` on its call to App B. The token is cached and only
        re-minted once it passes ~80% of its TTL, so a tight background loop doesn't
        hammer Duar. Requires this service to be an active member of an active
        realm (Duar rejects a standalone caller with 403).
        """
        if self._m2m_token is not None and time.monotonic() < self._m2m_refresh_at:
            return self._m2m_token
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/realm/m2m-token",
                json={},
                headers={"X-Service-Key": self.service_key},
            )
        if resp.status_code != 200:
            raise DuarError(f"m2m mint failed: {resp.status_code}", resp.status_code)
        data = resp.json()
        self._m2m_token = data["token"]
        self._m2m_refresh_at = time.monotonic() + data["expires_in"] * 0.8
        return self._m2m_token

    @property
    def require_system(self) -> Callable:
        """FastAPI dependency returning a ``SystemAuth`` for a no-user m2m call.

        Reads the m2m token from ``Authorization: Bearer`` (its only credential —
        there is no user). Raise this route's path in the middleware ``exclude_paths``.
        """

        def dependency(request: Request) -> SystemAuth:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Missing m2m token")
            try:
                return self.verify_m2m_token(auth.removeprefix("Bearer "))
            except DuarError as exc:
                raise HTTPException(status_code=exc.status_code or 401, detail=str(exc))

        return dependency
