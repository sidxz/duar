"""record_action_check: allowed → action_usage upsert, denied → action_denied
activity row; and a recording failure must never break the check-action
response (SDK require_action hot path).

Repo fake-session style — assert compiled SQL / added ORM rows, no real DB.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.api import role_routes
from src.api.dependencies import ServiceKeyContext, require_service_key
from src.api.role_routes import router as role_router
from src.auth.jwt import create_access_token
from src.database import get_db
from src.models.activity import ActivityLog
from src.services import role_service

WORKSPACE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


class _FakeDB:
    def __init__(self):
        self.statements = []
        self.added = []

    async def execute(self, stmt):
        self.statements.append(stmt)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


async def _record(db, allowed):
    await role_service.record_action_check(
        db,
        allowed=allowed,
        user_id=USER_ID,
        service_name="notes",
        action="notes:read",
        workspace_id=WORKSPACE_ID,
    )


@pytest.mark.asyncio
async def test_allowed_upserts_daily_rollup():
    db = _FakeDB()
    await _record(db, allowed=True)
    assert db.added == []  # no per-event activity row on the hot path
    assert len(db.statements) == 1
    sql = str(db.statements[0].compile(dialect=postgresql.dialect())).lower()
    assert "insert into action_usage" in sql
    assert "on conflict" in sql
    # conflict target is the full composite key
    for col in ("day", "workspace_id", "user_id", "service_name", "action"):
        assert col in sql
    assert "action_usage.count +" in sql  # increments, not overwrites


@pytest.mark.asyncio
async def test_denied_writes_action_denied_activity_row():
    db = _FakeDB()
    await _record(db, allowed=False)
    assert db.statements == []  # no rollup write for denials
    assert len(db.added) == 1
    row = db.added[0]
    assert isinstance(row, ActivityLog)
    assert row.action == "action_denied"
    assert row.actor_id == USER_ID
    assert row.target_type == "user"
    assert row.target_id == USER_ID
    assert row.workspace_id == WORKSPACE_ID
    assert row.detail == {"service_name": "notes", "action": "notes:read"}


class _ExplodingDB(_FakeDB):
    async def execute(self, stmt):
        raise RuntimeError("db down")

    async def commit(self):
        raise AssertionError("commit must not be reached")

    async def rollback(self):
        pass


def test_recording_failure_does_not_break_check_response(monkeypatch):
    from src.services import token_service

    async def _false(_arg):
        return False

    monkeypatch.setattr(token_service, "is_access_token_blacklisted", _false)
    monkeypatch.setattr(token_service, "is_user_deactivated", _false)

    async def _check(_db, **_kw):
        return True, ["editor-role"]

    monkeypatch.setattr(role_routes.role_service, "check_action", _check)

    app = FastAPI()
    app.include_router(role_router)
    app.dependency_overrides[require_service_key] = lambda: ServiceKeyContext(
        service_name="notes"
    )

    async def _db():
        yield _ExplodingDB()

    app.dependency_overrides[get_db] = _db

    token = create_access_token(
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
    resp = TestClient(app).post(
        "/roles/check-action",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "service_name": "notes",
            "action": "notes:read",
            "workspace_id": str(WORKSPACE_ID),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"allowed": True, "roles": ["editor-role"]}
