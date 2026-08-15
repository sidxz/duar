"""Tests for RequestAuth — the per-request auth context."""

import uuid
from typing import Protocol, runtime_checkable

import httpx
import pytest
import respx

from duar_auth.auth import RequestAuth
from duar_auth.permissions import PermissionClient
from duar_auth.roles import RoleClient
from duar_auth.types import AuthenticatedUser, DuarError


def _make_user(role: str = "editor") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        email="alice@example.com",
        name="Alice",
        workspace_id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        workspace_slug="acme",
        workspace_role=role,
        groups=[uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")],
    )


TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.fake"
RES_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


class TestForwardedProperties:
    """RequestAuth should forward identity properties from the wrapped user."""

    def test_user_id(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        assert auth.user_id == uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    def test_workspace_id(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        assert auth.workspace_id == uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    def test_workspace_role(self):
        auth = RequestAuth(user=_make_user("admin"), _token=TOKEN)
        assert auth.workspace_role == "admin"

    def test_email(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        assert auth.email == "alice@example.com"

    def test_name(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        assert auth.name == "Alice"

    def test_groups(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        assert auth.groups == [uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")]

    def test_is_admin(self):
        assert RequestAuth(user=_make_user("owner"), _token=TOKEN).is_admin is True
        assert RequestAuth(user=_make_user("admin"), _token=TOKEN).is_admin is True
        assert RequestAuth(user=_make_user("editor"), _token=TOKEN).is_admin is False

    def test_is_editor(self):
        assert RequestAuth(user=_make_user("editor"), _token=TOKEN).is_editor is True
        assert RequestAuth(user=_make_user("viewer"), _token=TOKEN).is_editor is False


def test_request_auth_forwards_org_fields():
    oid = uuid.uuid4()
    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="a@x.com",
        name="A",
        workspace_id=uuid.uuid4(),
        workspace_slug="ws",
        workspace_role="editor",
        org_id=oid,
        org_slug="abbvie",
        org_is_public=True,
    )
    auth = RequestAuth(user=user, _token="tok")
    assert auth.org_id == oid
    assert auth.org_slug == "abbvie"
    assert auth.org_is_public is True


class TestHasRole:
    def test_delegates_to_user(self):
        auth = RequestAuth(user=_make_user("admin"), _token=TOKEN)
        assert auth.has_role("viewer") is True
        assert auth.has_role("editor") is True
        assert auth.has_role("admin") is True
        assert auth.has_role("owner") is False


class TestCanWithoutClient:
    async def test_raises_duar_error(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        with pytest.raises(DuarError, match="PermissionClient not configured"):
            await auth.can("document", RES_ID, "view")


class TestCheckActionWithoutClient:
    async def test_raises_duar_error(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        with pytest.raises(DuarError, match="RoleClient not configured"):
            await auth.check_action("reports:export")


class TestAccessibleWithoutClient:
    async def test_raises_duar_error(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        with pytest.raises(DuarError, match="PermissionClient not configured"):
            await auth.accessible("document", "view")


class TestRegisterResourceWithoutClient:
    async def test_raises_duar_error(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        with pytest.raises(DuarError, match="PermissionClient not configured"):
            await auth.register_resource("document", RES_ID)


class TestCanDelegatesToPermissionClient:
    @respx.mock
    async def test_passes_token_and_returns_result(self):
        pc = PermissionClient("https://auth.test", "docu-store", service_key="sk-test")
        respx.post("https://auth.test/permissions/check").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "service_name": "docu-store",
                            "resource_type": "document",
                            "resource_id": str(RES_ID),
                            "action": "view",
                            "allowed": True,
                        }
                    ]
                },
            )
        )
        auth = RequestAuth(user=_make_user(), _token=TOKEN, _permissions=pc)
        result = await auth.can("document", RES_ID, "view")
        assert result is True

        # Verify the token was passed through
        request = respx.calls[0].request
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"

    @respx.mock
    async def test_returns_false_when_denied(self):
        pc = PermissionClient("https://auth.test", "docu-store", service_key="sk-test")
        respx.post("https://auth.test/permissions/check").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "service_name": "docu-store",
                            "resource_type": "document",
                            "resource_id": str(RES_ID),
                            "action": "edit",
                            "allowed": False,
                        }
                    ]
                },
            )
        )
        auth = RequestAuth(user=_make_user(), _token=TOKEN, _permissions=pc)
        assert await auth.can("document", RES_ID, "edit") is False


class TestCheckActionDelegatesToRoleClient:
    @respx.mock
    async def test_passes_token_and_workspace(self):
        rc = RoleClient("https://auth.test", "docu-store", service_key="sk-test")
        respx.post("https://auth.test/roles/check-action").mock(
            return_value=httpx.Response(200, json={"allowed": True})
        )
        auth = RequestAuth(user=_make_user(), _token=TOKEN, _roles=rc)
        result = await auth.check_action("reports:export")
        assert result is True

        # Verify token and workspace_id were passed
        request = respx.calls[0].request
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        import json

        body = json.loads(request.content)
        assert body["workspace_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class TestAccessibleDelegates:
    @respx.mock
    async def test_returns_resource_ids_and_full_access(self):
        pc = PermissionClient("https://auth.test", "docu-store", service_key="sk-test")
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        respx.post("https://auth.test/permissions/accessible").mock(
            return_value=httpx.Response(
                200,
                json={"resource_ids": [str(r1), str(r2)], "has_full_access": False},
            )
        )
        auth = RequestAuth(user=_make_user(), _token=TOKEN, _permissions=pc)
        ids, full = await auth.accessible("document", "view")
        assert len(ids) == 2
        assert full is False


class TestRegisterResourceUsesUserContext:
    @respx.mock
    async def test_uses_workspace_and_owner_from_user(self):
        pc = PermissionClient("https://auth.test", "docu-store", service_key="sk-test")
        respx.post("https://auth.test/permissions/register").mock(
            return_value=httpx.Response(200, json={"id": "perm-123"})
        )
        auth = RequestAuth(user=_make_user(), _token=TOKEN, _permissions=pc)
        result = await auth.register_resource("document", RES_ID)
        assert result == {"id": "perm-123"}

        import json

        request = respx.calls[0].request
        body = json.loads(request.content)
        assert body["workspace_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        assert body["owner_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        assert body["visibility"] == "workspace"


class TestProtocolStructuralCompatibility:
    """Verify RequestAuth satisfies a DDD-style Protocol without SDK imports."""

    def test_satisfies_auth_context_protocol(self):
        @runtime_checkable
        class AuthContext(Protocol):
            @property
            def user_id(self) -> uuid.UUID: ...
            @property
            def workspace_id(self) -> uuid.UUID: ...
            @property
            def workspace_role(self) -> str: ...
            def has_role(self, minimum_role: str) -> bool: ...

        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        assert isinstance(auth, AuthContext)


class TestTokenNotExposed:
    """The raw JWT token should not appear in repr."""

    def test_repr_hides_token(self):
        auth = RequestAuth(user=_make_user(), _token=TOKEN)
        assert TOKEN not in repr(auth)


class TestRequestDedup:
    """RequestAuth should deduplicate identical calls within the same request."""

    @respx.mock
    async def test_accessible_deduped(self):
        pc = PermissionClient("https://auth.test", "docu-store", service_key="sk-test")
        r1 = uuid.uuid4()
        route = respx.post("https://auth.test/permissions/accessible").mock(
            return_value=httpx.Response(
                200,
                json={"resource_ids": [str(r1)], "has_full_access": False},
            )
        )
        auth = RequestAuth(user=_make_user(), _token=TOKEN, _permissions=pc)

        # Two identical calls within the same request
        ids1, _ = await auth.accessible("document", "view")
        ids2, _ = await auth.accessible("document", "view")
        assert ids1 == ids2
        assert route.call_count == 1  # only one HTTP call

    @respx.mock
    async def test_can_deduped(self):
        pc = PermissionClient("https://auth.test", "docu-store", service_key="sk-test")
        route = respx.post("https://auth.test/permissions/check").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "service_name": "docu-store",
                            "resource_type": "document",
                            "resource_id": str(RES_ID),
                            "action": "view",
                            "allowed": True,
                        }
                    ]
                },
            )
        )
        auth = RequestAuth(user=_make_user(), _token=TOKEN, _permissions=pc)

        assert await auth.can("document", RES_ID, "view") is True
        assert await auth.can("document", RES_ID, "view") is True
        assert route.call_count == 1

    @respx.mock
    async def test_different_args_not_deduped(self):
        pc = PermissionClient("https://auth.test", "docu-store", service_key="sk-test")
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        route = respx.post("https://auth.test/permissions/check").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "service_name": "docu-store",
                            "resource_type": "document",
                            "resource_id": str(r1),
                            "action": "view",
                            "allowed": True,
                        }
                    ]
                },
            )
        )
        auth = RequestAuth(user=_make_user(), _token=TOKEN, _permissions=pc)

        await auth.can("document", r1, "view")
        await auth.can("document", r2, "view")
        assert route.call_count == 2  # different resource IDs → different calls

    @respx.mock
    async def test_check_action_deduped(self):
        rc = RoleClient("https://auth.test", "docu-store", service_key="sk-test")
        route = respx.post("https://auth.test/roles/check-action").mock(
            return_value=httpx.Response(200, json={"allowed": True})
        )
        auth = RequestAuth(user=_make_user(), _token=TOKEN, _roles=rc)

        assert await auth.check_action("reports:export") is True
        assert await auth.check_action("reports:export") is True
        assert route.call_count == 1
