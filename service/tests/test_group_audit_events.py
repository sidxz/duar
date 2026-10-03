"""Group mutations must emit activity events.

Groups grant entity-ACL shares and (since group_roles) RBAC actions — an
unaudited group-member-add is an unaudited privilege grant. Same fake-dep
style as test_org_admin_routes.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import CurrentUser, get_current_user
from src.api.group_routes import router as group_router
from src.database import get_db
from src.middleware.rate_limit import limiter

WS_ID = uuid.uuid4()
ACTOR_ID = uuid.uuid4()
GROUP_ID = uuid.uuid4()
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


def _build_app():
    app = FastAPI()
    app.include_router(group_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=ACTOR_ID, workspace_id=WS_ID, workspace_role="admin", groups=[]
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


def _group_ns():
    return SimpleNamespace(
        id=GROUP_ID,
        workspace_id=WS_ID,
        name="analysts",
        description=None,
        created_by=ACTOR_ID,
        created_at=datetime.now(UTC),
    )


def test_create_group_audited(monkeypatch, activity):
    from src.api import group_routes

    async def _create(_db, **kw):
        return _group_ns()

    monkeypatch.setattr(group_routes.group_service, "create_group", _create)
    resp = _build_app().post(f"/workspaces/{WS_ID}/groups", json={"name": "analysts"})
    assert resp.status_code == 201
    assert activity[0]["action"] == "group_created"
    assert activity[0]["target_type"] == "group"
    assert activity[0]["target_id"] == GROUP_ID
    assert activity[0]["actor_id"] == ACTOR_ID
    assert activity[0]["workspace_id"] == WS_ID


def test_update_group_audited(monkeypatch, activity):
    from src.api import group_routes

    async def _update(_db, group_id, workspace_id, **kw):
        return _group_ns()

    monkeypatch.setattr(group_routes.group_service, "update_group", _update)
    resp = _build_app().patch(
        f"/workspaces/{WS_ID}/groups/{GROUP_ID}", json={"name": "renamed"}
    )
    assert resp.status_code == 200
    assert activity[0]["action"] == "group_updated"
    assert activity[0]["target_id"] == GROUP_ID
    assert activity[0]["actor_id"] == ACTOR_ID


def test_delete_group_audited(monkeypatch, activity):
    from src.api import group_routes

    async def _delete(_db, group_id, workspace_id):
        pass

    monkeypatch.setattr(group_routes.group_service, "delete_group", _delete)
    resp = _build_app().delete(f"/workspaces/{WS_ID}/groups/{GROUP_ID}")
    assert resp.status_code == 204
    assert activity[0]["action"] == "group_deleted"
    assert activity[0]["target_id"] == GROUP_ID


def test_add_member_audited(monkeypatch, activity):
    from src.api import group_routes

    async def _add(_db, group_id, workspace_id, user_id):
        pass

    monkeypatch.setattr(group_routes.group_service, "add_member", _add)
    resp = _build_app().post(
        f"/workspaces/{WS_ID}/groups/{GROUP_ID}/members/{MEMBER_ID}"
    )
    assert resp.status_code == 201
    assert activity[0]["action"] == "group_member_added"
    assert activity[0]["target_type"] == "user"
    assert activity[0]["target_id"] == MEMBER_ID
    assert activity[0]["detail"] == {"group_id": str(GROUP_ID)}
    assert activity[0]["workspace_id"] == WS_ID


def test_remove_member_audited(monkeypatch, activity):
    from src.api import group_routes

    async def _remove(_db, group_id, workspace_id, user_id):
        pass

    monkeypatch.setattr(group_routes.group_service, "remove_member", _remove)
    resp = _build_app().delete(
        f"/workspaces/{WS_ID}/groups/{GROUP_ID}/members/{MEMBER_ID}"
    )
    assert resp.status_code == 204
    assert activity[0]["action"] == "group_member_removed"


def test_failed_mutation_not_audited(monkeypatch, activity):
    from src.api import group_routes

    async def _add(_db, group_id, workspace_id, user_id):
        raise ValueError("Group not found")

    monkeypatch.setattr(group_routes.group_service, "add_member", _add)
    resp = _build_app().post(
        f"/workspaces/{WS_ID}/groups/{GROUP_ID}/members/{MEMBER_ID}"
    )
    assert resp.status_code == 404
    assert activity == []
