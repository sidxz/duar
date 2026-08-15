"""Regression: /roles/check-action + /roles/user-actions must accept authz tokens.

In authz mode a backend only holds the IdP token and the Duar authz token —
never a sentinel:access token. These endpoints used get_current_user (access-only),
which made tier-2 RBAC checks return 401 for every authz-mode caller. They must use
the same dual access/authz dependency as the permission routes, including the
svc-claim binding that blocks cross-service replay.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import role_routes
from src.api.dependencies import ServiceKeyContext, require_service_key
from src.api.role_routes import router as role_router
from src.auth.jwt import create_access_token, create_authz_token
from src.database import get_db

WORKSPACE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _stub_token_hygiene(monkeypatch):
    """Token hygiene checks hit Redis; stub them to 'not revoked, not deactivated'."""
    from src.services import token_service

    async def _false(_arg):
        return False

    monkeypatch.setattr(token_service, "is_access_token_blacklisted", _false)
    monkeypatch.setattr(token_service, "is_user_deactivated", _false)


def _build_app():
    app = FastAPI()
    app.include_router(role_router)
    app.dependency_overrides[require_service_key] = lambda: ServiceKeyContext(
        service_name="notes"
    )

    async def _db():
        yield None

    app.dependency_overrides[get_db] = _db
    return app


def _authz_token(service_name="notes"):
    return create_authz_token(
        user_id=USER_ID,
        idp_sub="google|12345",
        workspace_id=WORKSPACE_ID,
        workspace_slug="acme",
        workspace_role="editor",
        actions=["notes:read"],
        service_name=service_name,
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )


def _access_token():
    return create_access_token(
        user_id=USER_ID,
        email="u@example.com",
        name="U",
        workspace_id=WORKSPACE_ID,
        workspace_slug="acme",
        workspace_role="editor",
        groups=[],
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )


def _post_check(client, token):
    return client.post(
        "/roles/check-action",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "service_name": "notes",
            "action": "notes:read",
            "workspace_id": str(WORKSPACE_ID),
        },
    )


def test_check_action_accepts_authz_token(monkeypatch):
    async def _check(_db, *, user_id, service_name, action, workspace_id):
        assert user_id == USER_ID
        return True, ["editor-role"]

    monkeypatch.setattr(role_routes.role_service, "check_action", _check)
    resp = _post_check(TestClient(_build_app()), _authz_token())
    assert resp.status_code == 200
    assert resp.json() == {"allowed": True, "roles": ["editor-role"]}


def test_check_action_rejects_authz_token_minted_for_other_service():
    resp = _post_check(TestClient(_build_app()), _authz_token(service_name="crm"))
    assert resp.status_code == 403


def test_check_action_still_accepts_access_token(monkeypatch):
    async def _check(_db, **_kw):
        return True, []

    monkeypatch.setattr(role_routes.role_service, "check_action", _check)
    resp = _post_check(TestClient(_build_app()), _access_token())
    assert resp.status_code == 200


def test_user_actions_accepts_authz_token(monkeypatch):
    async def _actions(_db, *, user_id, service_name, workspace_id):
        return ["notes:read"]

    monkeypatch.setattr(role_routes.role_service, "get_user_actions", _actions)
    resp = TestClient(_build_app()).get(
        "/roles/user-actions",
        headers={"Authorization": f"Bearer {_authz_token()}"},
        params={"service_name": "notes", "workspace_id": str(WORKSPACE_ID)},
    )
    assert resp.status_code == 200
    assert resp.json() == {"actions": ["notes:read"]}
