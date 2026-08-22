"""Tests for AuthzClient."""

import uuid

import pytest
import respx
from httpx import Response

from duar_auth.authz import AuthzClient
from duar_auth.types import DuarError


class TestAuthzClient:
    @pytest.mark.asyncio
    async def test_resolve_with_workspace(self):
        user_id = str(uuid.uuid4())
        workspace_id = str(uuid.uuid4())
        mock_response = {
            "user": {"id": user_id, "email": "alice@acme.com", "name": "Alice"},
            "workspace": {"id": workspace_id, "slug": "acme", "role": "editor"},
            "authz_token": "eyJ...",
            "expires_in": 300,
            "workspaces": None,
        }
        with respx.mock:
            respx.post("http://duar:9003/authz/resolve").mock(return_value=Response(200, json=mock_response))
            async with AuthzClient("http://duar:9003", service_key="sk_test") as client:
                result = await client.resolve(
                    idp_token="fake-idp-token",
                    provider="google",
                    workspace_id=uuid.UUID(workspace_id),
                )
                assert result["authz_token"] == "eyJ..."
                assert result["user"]["email"] == "alice@acme.com"

    @pytest.mark.asyncio
    async def test_resolve_workspace_list(self):
        mock_response = {
            "user": {"id": str(uuid.uuid4()), "email": "alice@acme.com", "name": "Alice"},
            "workspaces": [
                {"id": str(uuid.uuid4()), "name": "Acme", "slug": "acme", "role": "editor"},
            ],
            "workspace": None,
            "authz_token": None,
            "expires_in": None,
        }
        with respx.mock:
            respx.post("http://duar:9003/authz/resolve").mock(return_value=Response(200, json=mock_response))
            async with AuthzClient("http://duar:9003", service_key="sk_test") as client:
                result = await client.resolve(idp_token="fake", provider="google")
                assert len(result["workspaces"]) == 1

    @pytest.mark.asyncio
    async def test_error_carries_status_and_retry_after(self):
        with respx.mock:
            respx.post("http://duar:9003/authz/resolve").mock(
                return_value=Response(429, json={"detail": "slow down"}, headers={"Retry-After": "17"})
            )
            async with AuthzClient("http://duar:9003", service_key="sk_test") as client:
                with pytest.raises(DuarError) as exc_info:
                    await client.resolve(idp_token="t", provider="google", workspace_id=uuid.uuid4())
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == "17"
