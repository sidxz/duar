"""Admin role↔group binding routes: guard + wiring tests with overridden deps.

Mirrors tests/test_org_admin_routes.py: minimal app, limiter disabled,
require_admin + get_db overridden, service layer monkeypatched, driven by
TestClient. Asserts the ValueError→status mapping and the audit events.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.admin_routes import router as admin_router
from src.api.dependencies import require_admin
from src.database import get_db
from src.middleware.rate_limit import limiter

ADMIN_ID = uuid.uuid4()
ROLE_ID = uuid.uuid4()
GROUP_ID = uuid.uuid4()


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
    app.include_router(admin_router)
    app.dependency_overrides[require_admin] = lambda: {"sub": str(ADMIN_ID)}

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


def test_assign_group_201_and_audited(monkeypatch, activity):
    from src.api import admin_routes

    async def _assign(_db, group_id, role_id, assigned_by=None):
        assert (group_id, role_id) == (GROUP_ID, ROLE_ID)
        assert assigned_by == ADMIN_ID

    monkeypatch.setattr(admin_routes.role_service, "assign_group_role", _assign)
    resp = _build_app().post(f"/admin/roles/{ROLE_ID}/groups/{GROUP_ID}")
    assert resp.status_code == 201
    assert activity[0]["action"] == "role_group_added"
    assert activity[0]["target_type"] == "group"
    assert activity[0]["target_id"] == GROUP_ID
    assert activity[0]["detail"] == {"role_id": str(ROLE_ID)}


@pytest.mark.parametrize(
    "error,status",
    [
        ("Role not found", 404),
        ("Group not found", 404),
        ("Group and role belong to different workspaces", 400),
        ("Group is already assigned to this role", 409),
    ],
)
def test_assign_group_error_mapping(monkeypatch, activity, error, status):
    from src.api import admin_routes

    async def _assign(_db, group_id, role_id, assigned_by=None):
        raise ValueError(error)

    monkeypatch.setattr(admin_routes.role_service, "assign_group_role", _assign)
    resp = _build_app().post(f"/admin/roles/{ROLE_ID}/groups/{GROUP_ID}")
    assert resp.status_code == status
    assert activity == []  # failed assigns must not be audited as grants


def test_remove_group_204_and_audited(monkeypatch, activity):
    from src.api import admin_routes

    async def _remove(_db, group_id, role_id):
        assert (group_id, role_id) == (GROUP_ID, ROLE_ID)

    monkeypatch.setattr(admin_routes.role_service, "remove_group_role", _remove)
    resp = _build_app().delete(f"/admin/roles/{ROLE_ID}/groups/{GROUP_ID}")
    assert resp.status_code == 204
    assert activity[0]["action"] == "role_group_removed"


def test_remove_group_missing_404(monkeypatch, activity):
    from src.api import admin_routes

    async def _remove(_db, group_id, role_id):
        raise ValueError("Group role not found")

    monkeypatch.setattr(admin_routes.role_service, "remove_group_role", _remove)
    resp = _build_app().delete(f"/admin/roles/{ROLE_ID}/groups/{GROUP_ID}")
    assert resp.status_code == 404
    assert activity == []


def test_list_role_groups(monkeypatch):
    from src.api import admin_routes

    now = datetime.now(UTC)

    async def _list(_db, role_id):
        return [
            {
                "group_id": GROUP_ID,
                "name": "analysts",
                "description": None,
                "member_count": 3,
                "assigned_at": now,
                "assigned_by": ADMIN_ID,
            }
        ]

    monkeypatch.setattr(admin_routes.role_service, "list_role_groups", _list)
    resp = _build_app().get(f"/admin/roles/{ROLE_ID}/groups")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["group_id"] == str(GROUP_ID)
    assert body[0]["member_count"] == 3
