"""Tests for RoleClient using respx to mock httpx."""

import uuid

import httpx
import pytest
import respx

from duar_auth.roles import RoleClient


@pytest.fixture()
def client():
    return RoleClient("https://auth.test", "docu-store", service_key="sk-test")


WS_ID = uuid.uuid4()


class TestRoleClient:
    @respx.mock
    async def test_register_actions(self, client):
        respx.post("https://auth.test/roles/actions/register").mock(
            return_value=httpx.Response(200, json={"registered": 2})
        )
        actions = [
            {"action": "reports:export", "description": "Export reports"},
            {"action": "reports:delete", "description": "Delete reports"},
        ]
        result = await client.register_actions(actions)
        assert result["registered"] == 2

    @respx.mock
    async def test_check_action_allowed(self, client):
        respx.post("https://auth.test/roles/check-action").mock(
            return_value=httpx.Response(200, json={"allowed": True})
        )
        assert await client.check_action("tok", "reports:export", WS_ID) is True

    @respx.mock
    async def test_check_action_denied(self, client):
        respx.post("https://auth.test/roles/check-action").mock(
            return_value=httpx.Response(200, json={"allowed": False})
        )
        assert await client.check_action("tok", "reports:export", WS_ID) is False

    @respx.mock
    async def test_get_user_actions(self, client):
        respx.get("https://auth.test/roles/user-actions").mock(
            return_value=httpx.Response(200, json={"actions": ["reports:export", "reports:view"]})
        )
        actions = await client.get_user_actions("tok", WS_ID)
        assert actions == ["reports:export", "reports:view"]

    async def test_context_manager(self):
        async with RoleClient("https://auth.test", "svc") as client:
            assert client.service_name == "svc"

    def test_headers(self, client):
        h = client._headers("jwt-tok")
        assert h["X-Service-Key"] == "sk-test"
        assert h["Authorization"] == "Bearer jwt-tok"

    @respx.mock
    async def test_register_actions_sends_correct_payload(self, client):
        route = respx.post("https://auth.test/roles/actions/register").mock(
            return_value=httpx.Response(200, json={"registered": 1})
        )
        await client.register_actions([{"action": "foo"}])
        request = route.calls[0].request
        import json

        body = json.loads(request.content)
        assert body["service_name"] == "docu-store"
        assert body["actions"] == [{"action": "foo"}]
