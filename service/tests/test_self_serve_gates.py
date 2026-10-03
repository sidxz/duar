"""SELF_SERVE_ENABLED gates on the legacy proxy-mode workspace routes.

Off (default): POST /workspaces is 403 — it was open to any user already holding a
workspace-scoped access token. On: direct member add is 403 (consent rule; use
invitations). Same fake-dep style as test_workspace_audit_events.py.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import CurrentUser, get_current_user
from src.api.workspace_routes import router as workspace_router
from src.config import settings
from src.database import get_db
from src.middleware.rate_limit import limiter

WS_ID = uuid.uuid4()
ACTOR_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


class _FakeDB:
    async def commit(self):
        pass

    async def get(self, model, pk):
        return SimpleNamespace(id=pk)


def _client(role="owner") -> TestClient:
    app = FastAPI()
    app.include_router(workspace_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=ACTOR_ID, workspace_id=WS_ID, workspace_role=role, groups=[]
    )

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_settings_defaults():
    # Construct fresh rather than assert on the live `settings` singleton: a
    # gitignored local .env (e.g. left by a manual click-through) can set
    # SELF_SERVE_ENABLED=true, which would make this test env-fragile.
    from src.config import Settings

    s = Settings(_env_file=None)
    assert s.self_serve_enabled is False
    assert s.self_serve_max_workspaces_per_user == 1
    assert s.self_serve_max_creates_per_hour == 30


def test_create_workspace_403_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    resp = _client().post("/workspaces", json={"name": "Acme", "slug": "acme"})
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"]


def test_direct_add_403_when_flag_on(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    resp = _client().post(
        f"/workspaces/{WS_ID}/members/invite",
        json={"email": "a@example.com", "role": "viewer"},
    )
    assert resp.status_code == 403
    assert "invitations" in resp.json()["detail"]


def test_direct_add_unchanged_when_flag_off(monkeypatch):
    """Flag off must not touch the legacy path: it reaches the service layer
    (which here raises 'User not found' through the fake) — i.e. NOT a 403."""
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    from src.api import workspace_routes

    async def _invite(*a, **kw):
        raise ValueError("User not found")

    monkeypatch.setattr(workspace_routes.workspace_service, "invite_member", _invite)
    resp = _client().post(
        f"/workspaces/{WS_ID}/members/invite",
        json={"email": "a@example.com", "role": "viewer"},
    )
    assert resp.status_code == 400


def test_create_workspace_cap_maps_to_403(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    from src.api import workspace_routes

    async def _boom(*a, **kw):
        raise workspace_routes.workspace_service.SelfServeCapReached()

    monkeypatch.setattr(workspace_routes.workspace_service, "create_self_serve", _boom)
    resp = _client().post("/workspaces", json={"name": "Acme", "slug": "acme"})
    assert resp.status_code == 403


def test_create_workspace_throttled_maps_to_429(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    from src.api import workspace_routes

    async def _boom(*a, **kw):
        raise workspace_routes.workspace_service.SelfServeThrottled()

    monkeypatch.setattr(workspace_routes.workspace_service, "create_self_serve", _boom)
    resp = _client().post("/workspaces", json={"name": "Acme", "slug": "acme"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "3600"
