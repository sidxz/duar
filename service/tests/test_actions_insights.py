"""Behavioral tests for actions_insights_service against in-memory SQLite.

The models are PG-flavored; two accommodations make them run on SQLite:
a JSONB→JSON compiler shim, and creating only the tables this feature
reads (client_apps has an ARRAY column SQLite can't render).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from src.api.admin_routes import router as admin_router
from src.api.dependencies import require_admin
from src.database import Base, get_db
from src.models.activity import ActivityLog
from src.models.role import (
    ActionUsage,
)
from src.models.user import User
from src.models.workspace import Workspace
from src.services.actions_insights_service import actions_insights
from src.services import actions_insights_service


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


_TABLES = [
    "users",
    "workspaces",
    "groups",
    "group_memberships",
    "service_actions",
    "roles",
    "role_actions",
    "user_roles",
    "group_roles",
    "action_usage",
    "activity_logs",
]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[Base.metadata.tables[n] for n in _TABLES]
            )
        )
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


TODAY = datetime.now(UTC).date()


def _user(email="u@example.com"):
    return User(id=uuid.uuid4(), email=email, name=email.split("@")[0])


def _workspace(name="Acme"):
    return Workspace(
        id=uuid.uuid4(), slug=name.lower() + uuid.uuid4().hex[:6], name=name
    )


def _usage(ws, user, service, action, count=1, days_ago=0):
    return ActionUsage(
        day=TODAY - timedelta(days=days_ago),
        workspace_id=ws.id,
        user_id=user.id,
        service_name=service,
        action=action,
        count=count,
    )


def _denied(ws, user, service, action, days_ago=0):
    return ActivityLog(
        action="action_denied",
        target_type="user",
        target_id=user.id,
        actor_id=user.id,
        workspace_id=ws.id,
        detail={"service_name": service, "action": action},
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


@pytest.mark.asyncio
async def test_empty_db_returns_empty_sections(db):
    out = await actions_insights(db, days=30)
    assert out["days"] == 30
    assert out["data_since"] is None
    assert out["top_actions"] == []
    assert out["by_service"] == []
    assert out["top_users"] == []
    assert out["trend"] == []


@pytest.mark.asyncio
async def test_usage_aggregates_rank_and_sum(db):
    ws = _workspace()
    alice, bob = _user("alice@example.com"), _user("bob@example.com")
    db.add_all([ws, alice, bob])
    db.add_all(
        [
            _usage(ws, alice, "notes", "notes:read", count=5),
            _usage(ws, alice, "notes", "notes:read", count=3, days_ago=1),
            _usage(ws, bob, "notes", "notes:write", count=4),
            _usage(ws, bob, "files", "files:read", count=2),
            # outside the 7-day window — must be excluded
            _usage(ws, bob, "files", "files:read", count=100, days_ago=10),
        ]
    )
    await db.flush()

    out = await actions_insights(db, days=7)

    assert out["top_actions"] == [
        {"service_name": "notes", "action": "notes:read", "count": 8},
        {"service_name": "notes", "action": "notes:write", "count": 4},
        {"service_name": "files", "action": "files:read", "count": 2},
    ]
    assert out["by_service"] == [
        {"service_name": "notes", "count": 12},
        {"service_name": "files", "count": 2},
    ]
    assert [u["email"] for u in out["top_users"]] == [
        "alice@example.com",
        "bob@example.com",
    ]
    assert out["top_users"][0]["count"] == 8
    assert out["data_since"] == (TODAY - timedelta(days=10)).isoformat()


@pytest.mark.asyncio
async def test_workspace_filter_excludes_other_workspaces(db):
    ws1, ws2 = _workspace("One"), _workspace("Two")
    u = _user()
    db.add_all([ws1, ws2, u])
    db.add_all(
        [
            _usage(ws1, u, "notes", "notes:read", count=5),
            _usage(ws2, u, "notes", "notes:read", count=9),
        ]
    )
    await db.flush()

    out = await actions_insights(db, days=7, workspace_id=ws1.id)
    assert out["top_actions"] == [
        {"service_name": "notes", "action": "notes:read", "count": 5}
    ]


@pytest.mark.asyncio
async def test_trend_merges_allowed_and_denied_by_day(db):
    ws = _workspace()
    u = _user()
    db.add_all([ws, u])
    db.add_all(
        [
            _usage(ws, u, "notes", "notes:read", count=5),
            _usage(ws, u, "notes", "notes:write", count=2, days_ago=1),
            _denied(ws, u, "notes", "notes:admin"),
            _denied(ws, u, "notes", "notes:admin"),
            _denied(ws, u, "notes", "notes:admin", days_ago=40),  # outside window
            # non-denied activity row — must NOT count into the denied series
            ActivityLog(
                action="user_login",
                target_type="user",
                target_id=u.id,
                actor_id=u.id,
                workspace_id=ws.id,
                created_at=datetime.now(UTC),
            ),
        ]
    )
    await db.flush()

    out = await actions_insights(db, days=30)
    trend = {t["day"]: t for t in out["trend"]}
    today = TODAY.isoformat()
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    assert trend[today]["allowed"] == 5
    assert trend[today]["denied"] == 2
    assert trend[yesterday]["allowed"] == 2
    assert trend[yesterday]["denied"] == 0
    assert len(out["trend"]) == 2  # only days with data; frontend fills gaps


def _grant_setup(db, ws):
    """Seed a service action + role granting it. Returns (svc_action, role)."""
    from src.models.role import Role, RoleAction, ServiceAction

    sa = ServiceAction(id=uuid.uuid4(), service_name="notes", action="notes:read")
    role = Role(id=uuid.uuid4(), workspace_id=ws.id, name="reader")
    db.add_all(
        [
            sa,
            role,
            RoleAction(id=uuid.uuid4(), role_id=role.id, service_action_id=sa.id),
        ]
    )
    return sa, role


@pytest.mark.asyncio
async def test_dormant_grants_direct_and_group_paths(db):
    from src.models.group import Group, GroupMembership
    from src.models.role import GroupRole, UserRole

    ws = _workspace()
    active, idle, grouped = (
        _user("active@example.com"),
        _user("idle@example.com"),
        _user("grouped@example.com"),
    )
    db.add_all([ws, active, idle, grouped])
    sa, role = _grant_setup(db, ws)
    # direct grants: active uses it, idle doesn't
    db.add_all(
        [
            UserRole(id=uuid.uuid4(), user_id=active.id, role_id=role.id),
            UserRole(id=uuid.uuid4(), user_id=idle.id, role_id=role.id),
            _usage(ws, active, "notes", "notes:read", count=3),
        ]
    )
    # group-path grant: grouped never uses it
    g = Group(id=uuid.uuid4(), workspace_id=ws.id, name="team")
    db.add_all(
        [
            g,
            GroupMembership(id=uuid.uuid4(), group_id=g.id, user_id=grouped.id),
            GroupRole(id=uuid.uuid4(), group_id=g.id, role_id=role.id),
        ]
    )
    await db.flush()

    out = await actions_insights(db, days=30)
    dormant = out["dormant_grants"]
    assert dormant["total"] == 2
    emails = {i["email"] for i in dormant["items"]}
    assert emails == {"idle@example.com", "grouped@example.com"}
    item = dormant["items"][0]
    assert item["service_name"] == "notes"
    assert item["action"] == "notes:read"
    assert item["role_name"] == "reader"
    assert item["workspace_name"] == ws.name


@pytest.mark.asyncio
async def test_dormant_grants_workspace_filter(db):
    from src.models.role import Role, RoleAction, ServiceAction, UserRole

    ws1, ws2 = _workspace("One"), _workspace("Two")
    u = _user()
    db.add_all([ws1, ws2, u])
    sa = ServiceAction(id=uuid.uuid4(), service_name="notes", action="notes:read")
    db.add(sa)
    for ws in (ws1, ws2):
        role = Role(id=uuid.uuid4(), workspace_id=ws.id, name=f"reader-{ws.name}")
        db.add_all(
            [
                role,
                RoleAction(id=uuid.uuid4(), role_id=role.id, service_action_id=sa.id),
                UserRole(id=uuid.uuid4(), user_id=u.id, role_id=role.id),
            ]
        )
    await db.flush()

    out = await actions_insights(db, days=30, workspace_id=ws1.id)
    assert out["dormant_grants"]["total"] == 1
    assert out["dormant_grants"]["items"][0]["role_name"] == "reader-One"


@pytest.mark.asyncio
async def test_unused_roles(db):
    from src.models.group import Group, GroupMembership
    from src.models.role import (
        GroupRole,
        Role,
        RoleAction,
        ServiceAction,
        UserRole,
    )

    ws = _workspace()
    active, idle, grouped = (
        _user("active@example.com"),
        _user("idle@example.com"),
        _user("grouped@example.com"),
    )
    db.add_all([ws, active, idle, grouped])

    def make_role(name, action):
        sa = ServiceAction(id=uuid.uuid4(), service_name="notes", action=action)
        role = Role(id=uuid.uuid4(), workspace_id=ws.id, name=name)
        db.add_all(
            [
                sa,
                role,
                RoleAction(id=uuid.uuid4(), role_id=role.id, service_action_id=sa.id),
            ]
        )
        return role

    used_role = make_role("used", "notes:read")
    idle_role = make_role("idle", "notes:write")
    make_role("empty", "notes:admin")
    group_used_role = make_role("group-used", "notes:share")

    g = Group(id=uuid.uuid4(), workspace_id=ws.id, name="team")
    db.add_all(
        [
            UserRole(id=uuid.uuid4(), user_id=active.id, role_id=used_role.id),
            UserRole(id=uuid.uuid4(), user_id=idle.id, role_id=idle_role.id),
            g,
            GroupMembership(id=uuid.uuid4(), group_id=g.id, user_id=grouped.id),
            GroupRole(id=uuid.uuid4(), group_id=g.id, role_id=group_used_role.id),
            _usage(ws, active, "notes", "notes:read", count=1),
            _usage(ws, grouped, "notes", "notes:share", count=1),
        ]
    )
    await db.flush()

    out = await actions_insights(db, days=30)
    by_name = {r["name"]: r for r in out["unused_roles"]}
    assert set(by_name) == {"idle", "empty"}
    assert by_name["idle"]["assignees"] == 1
    assert by_name["idle"]["no_assignees"] is False
    assert by_name["empty"]["assignees"] == 0
    assert by_name["empty"]["no_assignees"] is True
    assert by_name["idle"]["workspace_name"] == ws.name


def test_actions_insights_route_parameter_validation():
    """Route-level test for GET /admin/actions/insights parameter validation.

    Regression test for Pydantic v2 query-string coercion issue: query strings
    don't auto-coerce to Literal values. The route must validate explicitly.
    """
    app = FastAPI()
    app.include_router(admin_router)

    # Override require_admin to allow unauthenticated requests
    async def mock_require_admin():
        return {"sub": "admin-user"}

    # Override get_db to a no-op
    async def mock_get_db():
        yield object()

    app.dependency_overrides[require_admin] = mock_require_admin
    app.dependency_overrides[get_db] = mock_get_db

    # Monkeypatch actions_insights to return stub data
    with patch.object(actions_insights_service, "actions_insights") as mock_insights:
        mock_insights.return_value = {"days": 30, "top_actions": []}

        client = TestClient(app)

        # Valid days: 7, 30, 90 (including default)
        resp = client.get("/admin/actions/insights?days=30")
        assert resp.status_code == 200, f"days=30 failed: {resp.text}"

        resp = client.get("/admin/actions/insights?days=90")
        assert resp.status_code == 200, f"days=90 failed: {resp.text}"

        resp = client.get("/admin/actions/insights?days=7")
        assert resp.status_code == 200, f"days=7 failed: {resp.text}"

        # No param: uses default (30)
        resp = client.get("/admin/actions/insights")
        assert resp.status_code == 200, f"no days param failed: {resp.text}"

        # Invalid days: should return 422
        resp = client.get("/admin/actions/insights?days=15")
        assert resp.status_code == 422, (
            f"days=15 should fail with 422, got {resp.status_code}"
        )

        resp = client.get("/admin/actions/insights?days=100")
        assert resp.status_code == 422, (
            f"days=100 should fail with 422, got {resp.status_code}"
        )
