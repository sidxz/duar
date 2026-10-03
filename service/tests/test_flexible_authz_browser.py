"""Regression: get_current_user_flexible must accept the browser authz path.

In authz mode the SPA holds only the IdP token and its Duar-minted authz
token — never a duar:access token and never a service key. The SDK's
Duar-direct helpers (getProfile, searchMembers, listGroups, …) send the
IdP token as Bearer plus the authz token in X-Authz-Token. The server must
authenticate the X-Authz-Token (Duar-signed, short-TTL) — previously the
header was never read and every such call 401'd.

Invariants preserved:
- A naked authz token in the Bearer slot (no service key) is still rejected.
- An access token in X-Authz-Token is rejected (audience mismatch).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import CurrentUser, get_current_user_flexible
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

    @app.get("/whoami")
    async def whoami(user: CurrentUser = Depends(get_current_user_flexible)):
        return {"user_id": str(user.user_id), "workspace_id": str(user.workspace_id)}

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


def test_browser_authz_header_with_idp_bearer_is_accepted():
    """The shipped SDK wire shape: unverifiable IdP token as Bearer, authz
    token in X-Authz-Token. The authz token is the credential."""
    client = TestClient(_build_app())
    resp = client.get(
        "/whoami",
        headers={
            "Authorization": "Bearer some-opaque-idp-token",
            "X-Authz-Token": _authz_token(),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] == str(USER_ID)


def test_browser_authz_header_alone_is_accepted():
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Authz-Token": _authz_token()})
    assert resp.status_code == 200
    assert resp.json()["workspace_id"] == str(WORKSPACE_ID)


def test_access_token_in_authz_header_rejected():
    """X-Authz-Token must carry an authz-audience token, not an access token."""
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"X-Authz-Token": _access_token()})
    assert resp.status_code == 401


def test_bearer_access_token_still_accepted():
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {_access_token()}"})
    assert resp.status_code == 200


def test_naked_authz_bearer_still_rejected():
    """Without a service key, an authz token in the Bearer slot stays rejected —
    the browser path requires the explicit X-Authz-Token header."""
    client = TestClient(_build_app())
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {_authz_token()}"})
    assert resp.status_code == 401
