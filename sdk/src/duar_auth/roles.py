"""Async HTTP client for checking RBAC actions against the identity service."""

import uuid

import httpx

from duar_auth._utils import warn_if_insecure
from duar_auth.types import DuarError


class RoleClient:
    """Client for the identity service's RBAC role/action API."""

    def __init__(self, base_url: str, service_name: str, service_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.service_name = service_name
        self.service_key = service_key
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=5.0)
        warn_if_insecure(self.base_url, "RoleClient")

    def __repr__(self) -> str:
        return f"RoleClient(base_url={self.base_url!r}, service_name={self.service_name!r})"

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

    async def register_actions(self, actions: list[dict]) -> dict:
        """Register actions for this service (service-key only, no user JWT needed).

        Each action dict should have 'action' (str) and optionally 'description' (str).
        """
        response = await self._client.post(
            "/roles/actions/register",
            headers=self._headers(),
            json={
                "service_name": self.service_name,
                "actions": actions,
            },
        )
        self._check(response)
        return response.json()

    async def check_action(
        self,
        token: str,
        action: str,
        workspace_id: uuid.UUID,
    ) -> bool:
        """Check if the current user can perform an action. Returns True/False."""
        response = await self._client.post(
            "/roles/check-action",
            headers=self._headers(token),
            json={
                "service_name": self.service_name,
                "action": action,
                "workspace_id": str(workspace_id),
            },
        )
        self._check(response)
        return response.json()["allowed"]

    async def get_user_actions(
        self,
        token: str,
        workspace_id: uuid.UUID,
    ) -> list[str]:
        """List all actions the current user can perform in a workspace."""
        response = await self._client.get(
            "/roles/user-actions",
            headers=self._headers(token),
            params={
                "service_name": self.service_name,
                "workspace_id": str(workspace_id),
            },
        )
        self._check(response)
        return response.json()["actions"]

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
