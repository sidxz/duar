"""verify_service_scope + dual-auth svc-claim check honor the realm slug."""

import uuid

import pytest
from fastapi import HTTPException

from src.api.dependencies import ServiceKeyContext, verify_service_scope


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def test_scope_check_accepts_realm_slug_rejects_own_name():
    ctx = ServiceKeyContext(service_name="docs", realm_slug="acme-suite")
    verify_service_scope(ctx, "acme-suite")  # realm slug is the shared scope
    with pytest.raises(HTTPException):
        verify_service_scope(ctx, "docs")  # own name is no longer the scope


def test_scope_check_standalone_unchanged():
    ctx = ServiceKeyContext(service_name="docs")
    verify_service_scope(ctx, "docs")  # standalone: scope == service_name
    with pytest.raises(HTTPException):
        verify_service_scope(ctx, "sheets")


def _realm_token(realm_slug: str) -> str:
    from src.auth.jwt import create_authz_token

    return create_authz_token(
        user_id=uuid.uuid4(),
        idp_sub="google|1",
        workspace_id=uuid.uuid4(),
        workspace_slug="w",
        workspace_role="editor",
        actions=["x"],
        service_name=realm_slug,  # minted under the REALM slug (what Plan 2 does)
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )


@pytest.mark.asyncio
async def test_dual_auth_accepts_realm_scoped_token(monkeypatch):
    from src.api import dependencies as deps

    async def _noop_hygiene(_payload):
        pass

    monkeypatch.setattr(deps, "_enforce_token_hygiene", _noop_hygiene)
    monkeypatch.setattr(deps, "bind_identity", lambda *a, **k: None)

    token = _realm_token("acme-suite")
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    # Caller is "docs" but a member of realm "acme-suite":
    svc_ctx = ServiceKeyContext(service_name="docs", realm_slug="acme-suite")
    user = await deps.get_user_for_service_call(req, svc_ctx)
    assert user.workspace_role == "editor"


@pytest.mark.asyncio
async def test_dual_auth_rejects_other_realm_token(monkeypatch):
    from src.api import dependencies as deps

    async def _noop_hygiene(_payload):
        pass

    monkeypatch.setattr(deps, "_enforce_token_hygiene", _noop_hygiene)
    monkeypatch.setattr(deps, "bind_identity", lambda *a, **k: None)

    token = _realm_token("other-realm")
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    svc_ctx = ServiceKeyContext(service_name="docs", realm_slug="acme-suite")
    with pytest.raises(HTTPException) as exc:
        await deps.get_user_for_service_call(req, svc_ctx)
    assert exc.value.status_code == 403
