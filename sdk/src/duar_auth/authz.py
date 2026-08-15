"""AuthZ mode client — resolve IdP tokens into authorization context."""

import uuid

import httpx

from duar_auth._utils import warn_if_insecure
from duar_auth.types import DuarError


class AuthzClient:
    """Client for Duar's AuthZ mode endpoints.

    Validates IdP tokens and retrieves authorization context
    (workspace roles, RBAC actions, signed authz JWT).
    """

    def __init__(self, base_url: str, service_key: str):
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        self._client: httpx.AsyncClient | None = None
        warn_if_insecure(self.base_url, "AuthzClient")

    def __repr__(self) -> str:
        return f"AuthzClient(base_url={self.base_url!r})"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"X-Service-Key": self.service_key}

    async def resolve(
        self,
        idp_token: str,
        provider: str,
        workspace_id: uuid.UUID | str | None = None,
        nonce: str | None = None,
    ) -> dict:
        """Resolve an IdP token into authorization context.

        Args:
            idp_token: Raw token from the IdP (OIDC ID token or OAuth access token).
            provider: IdP provider name ("google", "github", "entra_id").
            workspace_id: Optional workspace to authorize for.
            nonce: Optional replay-protection nonce. When provided, Duar
                requires the IdP token's ``nonce`` claim (OIDC only) to match.

        Returns:
            Dict with user info. If workspace_id was provided, includes
            authz_token and workspace. Otherwise includes workspaces list.
        """
        client = await self._get_client()
        body: dict = {"idp_token": idp_token, "provider": provider}
        if workspace_id:
            body["workspace_id"] = str(workspace_id)
        if nonce:
            body["nonce"] = nonce
        resp = await client.post(
            f"{self.base_url}/authz/resolve",
            json=body,
            headers=self._headers(),
        )
        if resp.status_code != 200:
            raise DuarError(f"Duar API error: {resp.status_code}", resp.status_code)
        return resp.json()

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
