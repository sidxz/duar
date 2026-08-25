"""Owner-path workspace mutations must emit activity events.

The admin panel routes have logged these actions all along; the user-facing
routes (owner/admin JWT) performed the same privilege mutations — including
promotion to owner and whole-workspace deletion — with no audit trail. Same
fake-dep style as test_group_audit_events.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
MEMBER_ID = uuid.uuid4()


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


def _build_app(role="owner"):
    app = FastAPI()
    app.include_router(workspace_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=ACTOR_ID, workspace_id=WS_ID, workspace_role=role, groups=[]
    )

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


@pytest.fixture
def activity(monkeypatch):
    from src.services import activity_service

    recorded = []

    async def _log(_db, **kw):
        recorded.append(kw)

    monkeypatch.setattr(activity_service, "log_activity", _log)
    return recorded


def _workspace_ns(ws_id=WS_ID):
    return SimpleNamespace(
        id=ws_id,
        slug="acme",
        name="Acme",
        description=None,
        created_by=ACTOR_ID,
        created_at=datetime.now(UTC),
    )


def test_create_workspace_audited(monkeypatch, activity):
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    from src.api import workspace_routes

    new_id = uuid.uuid4()

    async def _create(_db, _actor, name, slug=None):
        return _workspace_ns(ws_id=new_id)

    monkeypatch.setattr(
        workspace_routes.workspace_service, "create_self_serve", _create
    )
    resp = _build_app().post("/workspaces", json={"name": "Acme", "slug": "acme"})
    assert resp.status_code == 201
    assert activity[0]["action"] == "workspace_created"
    assert activity[0]["target_id"] == new_id
    assert activity[0]["actor_id"] == ACTOR_ID
    assert activity[0]["detail"] == {"name": "Acme", "slug": "acme", "self_serve": True}


def test_update_workspace_audited(monkeypatch, activity):
    from src.api import workspace_routes

    async def _update(_db, _ws_id, **kw):
        return _workspace_ns()

    monkeypatch.setattr(workspace_routes.workspace_service, "update_workspace", _update)
    resp = _build_app(role="admin").patch(
        f"/workspaces/{WS_ID}", json={"name": "Renamed"}
    )
    assert resp.status_code == 200
    assert activity[0]["action"] == "workspace_updated"
    assert activity[0]["target_id"] == WS_ID


def test_delete_workspace_audited(monkeypatch, activity):
    from src.api import workspace_routes

    async def _get(_db, _ws_id):
        return _workspace_ns()

    async def _delete(_db, _ws_id):
        pass

    monkeypatch.setattr(workspace_routes.workspace_service, "get_workspace", _get)
    monkeypatch.setattr(workspace_routes.workspace_service, "delete_workspace", _delete)
    resp = _build_app().delete(f"/workspaces/{WS_ID}")
    assert resp.status_code == 204
    assert activity[0]["action"] == "workspace_deleted"
    assert activity[0]["target_id"] == WS_ID
    assert activity[0]["detail"] == {"name": "Acme", "slug": "acme"}


def test_invite_member_audited(monkeypatch, activity):
    from src.api import workspace_routes

    async def _invite(_db, _ws_id, **kw):
        return SimpleNamespace(
            user_id=MEMBER_ID,
            email="new@example.com",
            name="New User",
            avatar_url=None,
            role="viewer",
            joined_at=datetime.now(UTC),
        )

    monkeypatch.setattr(workspace_routes.workspace_service, "invite_member", _invite)
    resp = _build_app(role="admin").post(
        f"/workspaces/{WS_ID}/members/invite",
        json={"email": "new@example.com", "role": "viewer"},
    )
    assert resp.status_code == 201
    assert activity[0]["action"] == "member_invited"
    assert activity[0]["target_id"] == MEMBER_ID
    assert activity[0]["detail"] == {"email": "new@example.com", "role": "viewer"}


def test_update_member_role_audited(monkeypatch, activity):
    from src.api import workspace_routes

    async def _update(_db, _ws_id, _user_id, **kw):
        return {"status": "ok"}

    monkeypatch.setattr(
        workspace_routes.workspace_service, "update_member_role", _update
    )
    resp = _build_app().patch(
        f"/workspaces/{WS_ID}/members/{MEMBER_ID}", json={"role": "owner"}
    )
    assert resp.status_code == 200
    assert activity[0]["action"] == "member_role_changed"
    assert activity[0]["target_id"] == MEMBER_ID
    assert activity[0]["actor_id"] == ACTOR_ID
    assert activity[0]["detail"] == {"role": "owner"}


def test_remove_member_audited(monkeypatch, activity):
    from src.api import workspace_routes

    async def _remove(_db, _ws_id, _user_id, **kw):
        pass

    monkeypatch.setattr(workspace_routes.workspace_service, "remove_member", _remove)
    resp = _build_app(role="admin").delete(f"/workspaces/{WS_ID}/members/{MEMBER_ID}")
    assert resp.status_code == 204
    assert activity[0]["action"] == "member_removed"
    assert activity[0]["target_id"] == MEMBER_ID


def test_denied_role_change_not_audited(monkeypatch, activity):
    """A privilege-escalation attempt rejected by the service must not write
    a success-shaped audit row."""
    from src.api import workspace_routes

    async def _update(_db, _ws_id, _user_id, **kw):
        raise ValueError("Only workspace owners can grant the owner role")

    monkeypatch.setattr(
        workspace_routes.workspace_service, "update_member_role", _update
    )
    resp = _build_app(role="admin").patch(
        f"/workspaces/{WS_ID}/members/{MEMBER_ID}", json={"role": "owner"}
    )
    assert resp.status_code == 403
    assert activity == []
