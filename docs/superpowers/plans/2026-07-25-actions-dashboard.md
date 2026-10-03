# RBAC Actions Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admin dashboard over RBAC action-usage data — usage analytics (top actions/services/users, allowed-vs-denied trend) + role mining (dormant grants, unused roles) — as one aggregate endpoint and one shared React component mounted globally and per-workspace.

**Architecture:** One new service module (`actions_insights_service.py`) with plain SQL aggregates over `action_usage`, `activity_logs`, and the RBAC grant tables; one admin route; one shared `ActionsInsightsView` React component mounted at `/usage` and as a WorkspaceDetail tab. Behavioral tests run against in-memory SQLite (aiosqlite) with a 3-line JSONB compiler shim — feasibility already verified.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (service), React + TanStack Query + Recharts + Tailwind semantic tokens (admin).

**Spec:** `docs/superpowers/specs/2026-07-25-actions-dashboard-design.md`

## Global Constraints

- No new runtime dependencies; `aiosqlite>=0.21` is a **dev-extra** (test-only) dependency — already added to `service/pyproject.toml` + `uv.lock` in the working tree; Task 1 commits it.
- No DB migrations — read-only feature over existing tables.
- Route lives under the existing `/admin` router (admin-cookie auth via router-level `require_admin` — do not add per-route auth).
- `days` query param restricted to exactly {7, 30, 90} (422 otherwise, via `Literal`).
- Admin SPA: semantic theme tokens only (`text-muted-foreground`, `border-border`, `bg-card`, `var(--chart-series)`, `var(--chart-critical)` …) — **never** `zinc-*` or hard-coded colors.
- The new nav item is labeled **"Usage"** at route `/usage` (the spec said "Actions" but that nav label is already taken by `/service-actions`).
- Python tests: `cd service && uv run pytest tests/test_actions_insights.py -x -q`; full suite `cd service && uv run pytest`. Admin verification: `cd admin && npm run build` (tsc + vite).
- Run `make fmt` before each commit that touches Python.
- Cross-dialect rule for day-grouping: use `func.date(...)` (works on SQLite and Postgres — verified), **not** `date_trunc` (PG-only) and **not** `cast(x, Date)` (broken on SQLite).

---

### Task 1: Usage aggregates — service module + SQLite behavioral tests

**Files:**
- Create: `service/tests/test_actions_insights.py`
- Create: `service/src/services/actions_insights_service.py`
- Modify (already changed, just commit): `service/pyproject.toml`, `uv.lock` (aiosqlite dev extra)

**Interfaces:**
- Produces: `actions_insights_service.actions_insights(db: AsyncSession, days: int = 30, workspace_id: uuid.UUID | None = None) -> dict` returning keys `days`, `since`, `data_since`, `top_actions`, `by_service`, `top_users`, `trend` (role-mining keys `dormant_grants`, `unused_roles` are added in Task 2 — in this task return them as `{"total": 0, "items": []}` and `[]` placeholders so the dict shape is stable).
- Produces (test-side): the SQLite fixture + seed helpers reused by Task 2's tests.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_actions_insights.py`:

```python
"""Behavioral tests for actions_insights_service against in-memory SQLite.

The models are PG-flavored; two accommodations make them run on SQLite:
a JSONB→JSON compiler shim, and creating only the tables this feature
reads (client_apps has an ARRAY column SQLite can't render).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from src.database import Base
from src.models.activity import ActivityLog
from src.models.group import Group, GroupMembership
from src.models.role import (
    ActionUsage,
    GroupRole,
    Role,
    RoleAction,
    ServiceAction,
    UserRole,
)
from src.models.user import User
from src.models.workspace import Workspace
from src.services.actions_insights_service import actions_insights


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
    return Workspace(id=uuid.uuid4(), slug=name.lower() + uuid.uuid4().hex[:6], name=name)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_actions_insights.py -x -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.actions_insights_service'`

- [ ] **Step 3: Write the implementation**

Create `service/src/services/actions_insights_service.py`:

```python
"""RBAC action-usage insights: usage aggregates + role-mining anti-joins.

Reads the ``action_usage`` daily rollup (allowed checks) and ``action_denied``
activity events (denied checks). Role-mining sections cross-reference the
grant tables (user_roles + group_roles⋈group_memberships) to surface
granted-but-never-used pairs and roles nobody exercises.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.activity import ActivityLog
from src.models.role import ActionUsage
from src.models.user import User


def _day_iso(d) -> str:
    """date-or-string day → ISO string (func.date returns str on SQLite)."""
    return d.isoformat() if isinstance(d, date) else str(d)[:10]


async def actions_insights(
    db: AsyncSession, days: int = 30, workspace_id: uuid.UUID | None = None
) -> dict:
    now = datetime.now(UTC)
    since = (now - timedelta(days=days)).date()

    def scoped(stmt, col=ActionUsage.workspace_id):
        return stmt.where(col == workspace_id) if workspace_id else stmt

    total = func.sum(ActionUsage.count).label("count")

    top_actions = (
        await db.execute(
            scoped(
                select(ActionUsage.service_name, ActionUsage.action, total)
                .where(ActionUsage.day >= since)
                .group_by(ActionUsage.service_name, ActionUsage.action)
                .order_by(total.desc())
                .limit(20)
            )
        )
    ).all()

    by_service = (
        await db.execute(
            scoped(
                select(ActionUsage.service_name, total)
                .where(ActionUsage.day >= since)
                .group_by(ActionUsage.service_name)
                .order_by(total.desc())
            )
        )
    ).all()

    top_users = (
        await db.execute(
            scoped(
                select(ActionUsage.user_id, User.email, User.name, total)
                .join(User, User.id == ActionUsage.user_id)
                .where(ActionUsage.day >= since)
                .group_by(ActionUsage.user_id, User.email, User.name)
                .order_by(total.desc())
                .limit(20)
            )
        )
    ).all()

    allowed_rows = (
        await db.execute(
            scoped(
                select(ActionUsage.day, func.sum(ActionUsage.count))
                .where(ActionUsage.day >= since)
                .group_by(ActionUsage.day)
            )
        )
    ).all()
    denied_day = func.date(ActivityLog.created_at).label("day")
    denied_rows = (
        await db.execute(
            scoped(
                select(denied_day, func.count())
                .where(
                    ActivityLog.action == "action_denied",
                    ActivityLog.created_at >= now - timedelta(days=days),
                )
                .group_by(denied_day),
                col=ActivityLog.workspace_id,
            )
        )
    ).all()

    trend: dict[str, dict] = {}
    for d, n in allowed_rows:
        trend.setdefault(_day_iso(d), {"allowed": 0, "denied": 0})["allowed"] = n
    for d, n in denied_rows:
        trend.setdefault(_day_iso(d), {"allowed": 0, "denied": 0})["denied"] = n

    data_since = (
        await db.execute(select(func.min(ActionUsage.day)))
    ).scalar_one_or_none()

    return {
        "days": days,
        "since": since.isoformat(),
        "data_since": _day_iso(data_since) if data_since else None,
        "top_actions": [
            {"service_name": s, "action": a, "count": c} for s, a, c in top_actions
        ],
        "by_service": [{"service_name": s, "count": c} for s, c in by_service],
        "top_users": [
            {"user_id": str(u), "email": e, "name": n, "count": c}
            for u, e, n, c in top_users
        ],
        "trend": [{"day": d, **v} for d, v in sorted(trend.items())],
        # filled in by the role-mining pass (Task 2)
        "dormant_grants": {"total": 0, "items": []},
        "unused_roles": [],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_actions_insights.py -x -q`
Expected: 4 passed

- [ ] **Step 5: Format and commit**

```bash
make fmt
git add service/tests/test_actions_insights.py service/src/services/actions_insights_service.py service/pyproject.toml uv.lock
git commit -m "feat(service): actions-insights usage aggregates over action_usage rollup"
```

---

### Task 2: Role-mining — dormant grants + unused roles

**Files:**
- Modify: `service/src/services/actions_insights_service.py`
- Modify: `service/tests/test_actions_insights.py`

**Interfaces:**
- Consumes: Task 1's fixture/seed helpers and `actions_insights(...)`.
- Produces: real `dormant_grants` (`{"total": int, "items": [{user_id, email, name, service_name, action, role_name, workspace_id, workspace_name}]}`, items capped at 50, ordered by service, action, email) and `unused_roles` (`[{id, name, workspace_id, workspace_name, assignees, no_assignees}]`, ordered by workspace name then role name) in the same return dict.

- [ ] **Step 1: Write the failing tests**

Append to `service/tests/test_actions_insights.py`:

```python
def _grant_setup(db, ws):
    """Seed a service action + role granting it. Returns (svc_action, role)."""
    sa = ServiceAction(id=uuid.uuid4(), service_name="notes", action="notes:read")
    role = Role(id=uuid.uuid4(), workspace_id=ws.id, name="reader")
    db.add_all([sa, role, RoleAction(id=uuid.uuid4(), role_id=role.id, service_action_id=sa.id)])
    return sa, role


@pytest.mark.asyncio
async def test_dormant_grants_direct_and_group_paths(db):
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
    ws1, ws2 = _workspace("One"), _workspace("Two")
    u = _user()
    db.add_all([ws1, ws2, u])
    for ws in (ws1, ws2):
        sa = ServiceAction(id=uuid.uuid4(), service_name="notes", action="notes:read")
        role = Role(id=uuid.uuid4(), workspace_id=ws.id, name=f"reader-{ws.name}")
        db.add_all(
            [
                sa,
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
            [sa, role, RoleAction(id=uuid.uuid4(), role_id=role.id, service_action_id=sa.id)]
        )
        return role

    used_role = make_role("used", "notes:read")
    idle_role = make_role("idle", "notes:write")
    empty_role = make_role("empty", "notes:admin")
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_actions_insights.py -x -q`
Expected: the three new tests FAIL on the placeholder values (`total == 0`, `unused_roles == []`); the four Task-1 tests still pass.

- [ ] **Step 3: Implement the role-mining queries**

In `service/src/services/actions_insights_service.py`, extend the imports:

```python
from sqlalchemy import and_, exists, func, select

from src.models.group import GroupMembership
from src.models.role import (
    ActionUsage,
    GroupRole,
    Role,
    RoleAction,
    ServiceAction,
    UserRole,
)
from src.models.workspace import Workspace
```

Add module constant:

```python
_DORMANT_LIMIT = 50
```

Inside `actions_insights`, replace the two placeholder return values by computing (insert before the `return`):

```python
    # ── dormant grants: granted (user, service, action) pairs w/ no usage ──
    direct = (
        select(
            UserRole.user_id.label("user_id"),
            ServiceAction.service_name.label("service_name"),
            ServiceAction.action.label("action"),
            Role.name.label("role_name"),
            Role.workspace_id.label("workspace_id"),
        )
        .select_from(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .join(RoleAction, RoleAction.role_id == Role.id)
        .join(ServiceAction, RoleAction.service_action_id == ServiceAction.id)
    )
    via_group = (
        select(
            GroupMembership.user_id.label("user_id"),
            ServiceAction.service_name.label("service_name"),
            ServiceAction.action.label("action"),
            Role.name.label("role_name"),
            Role.workspace_id.label("workspace_id"),
        )
        .select_from(Role)
        .join(GroupRole, GroupRole.role_id == Role.id)
        .join(GroupMembership, GroupMembership.group_id == GroupRole.group_id)
        .join(RoleAction, RoleAction.role_id == Role.id)
        .join(ServiceAction, RoleAction.service_action_id == ServiceAction.id)
    )
    if workspace_id:
        direct = direct.where(Role.workspace_id == workspace_id)
        via_group = via_group.where(Role.workspace_id == workspace_id)
    granted = direct.union(via_group).subquery("granted")

    used = (
        select(1)
        .where(
            ActionUsage.user_id == granted.c.user_id,
            ActionUsage.workspace_id == granted.c.workspace_id,
            ActionUsage.service_name == granted.c.service_name,
            ActionUsage.action == granted.c.action,
            ActionUsage.day >= since,
        )
        .exists()
    )
    dormant_base = (
        select(
            granted.c.user_id,
            User.email,
            User.name,
            granted.c.service_name,
            granted.c.action,
            granted.c.role_name,
            granted.c.workspace_id,
            Workspace.name.label("workspace_name"),
        )
        .join(User, User.id == granted.c.user_id)
        .join(Workspace, Workspace.id == granted.c.workspace_id)
        .where(~used)
    )
    dormant_total = (
        await db.execute(select(func.count()).select_from(dormant_base.subquery()))
    ).scalar_one()
    dormant_rows = (
        await db.execute(
            dormant_base.order_by(
                granted.c.service_name, granted.c.action, User.email
            ).limit(_DORMANT_LIMIT)
        )
    ).all()

    # ── unused roles: no assignee exercised any of the role's actions ──
    direct_use = (
        select(1)
        .select_from(UserRole)
        .join(RoleAction, RoleAction.role_id == UserRole.role_id)
        .join(ServiceAction, ServiceAction.id == RoleAction.service_action_id)
        .join(
            ActionUsage,
            and_(
                ActionUsage.user_id == UserRole.user_id,
                ActionUsage.workspace_id == Role.workspace_id,
                ActionUsage.service_name == ServiceAction.service_name,
                ActionUsage.action == ServiceAction.action,
                ActionUsage.day >= since,
            ),
        )
        .where(UserRole.role_id == Role.id)
    )
    group_use = (
        select(1)
        .select_from(GroupRole)
        .join(GroupMembership, GroupMembership.group_id == GroupRole.group_id)
        .join(RoleAction, RoleAction.role_id == GroupRole.role_id)
        .join(ServiceAction, ServiceAction.id == RoleAction.service_action_id)
        .join(
            ActionUsage,
            and_(
                ActionUsage.user_id == GroupMembership.user_id,
                ActionUsage.workspace_id == Role.workspace_id,
                ActionUsage.service_name == ServiceAction.service_name,
                ActionUsage.action == ServiceAction.action,
                ActionUsage.day >= since,
            ),
        )
        .where(GroupRole.role_id == Role.id)
    )
    direct_assignees = (
        select(func.count()).where(UserRole.role_id == Role.id).scalar_subquery()
    )
    group_assignees = (
        select(func.count())
        .select_from(GroupRole)
        .join(GroupMembership, GroupMembership.group_id == GroupRole.group_id)
        .where(GroupRole.role_id == Role.id)
        .scalar_subquery()
    )
    unused_stmt = (
        select(
            Role.id,
            Role.name,
            Role.workspace_id,
            Workspace.name.label("workspace_name"),
            (direct_assignees + group_assignees).label("assignees"),
        )
        .join(Workspace, Workspace.id == Role.workspace_id)
        .where(~exists(direct_use), ~exists(group_use))
        .order_by(Workspace.name, Role.name)
    )
    if workspace_id:
        unused_stmt = unused_stmt.where(Role.workspace_id == workspace_id)
    unused_rows = (await db.execute(unused_stmt)).all()
```

And in the returned dict replace the placeholders:

```python
        "dormant_grants": {
            "total": dormant_total,
            "items": [
                {
                    "user_id": str(r.user_id),
                    "email": r.email,
                    "name": r.name,
                    "service_name": r.service_name,
                    "action": r.action,
                    "role_name": r.role_name,
                    "workspace_id": str(r.workspace_id),
                    "workspace_name": r.workspace_name,
                }
                for r in dormant_rows
            ],
        },
        "unused_roles": [
            {
                "id": str(r.id),
                "name": r.name,
                "workspace_id": str(r.workspace_id),
                "workspace_name": r.workspace_name,
                "assignees": r.assignees,
                "no_assignees": r.assignees == 0,
            }
            for r in unused_rows
        ],
```

Note: the `~exists(direct_use)` subqueries reference `Role` from the enclosing query; SQLAlchemy auto-correlates because `Role` is in the outer FROM. If a test fails with `Role` appearing inside the subquery's FROM, add `.correlate(Role)` to `direct_use`/`group_use`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_actions_insights.py -x -q`
Expected: 7 passed

- [ ] **Step 5: Format and commit**

```bash
make fmt
git add service/src/services/actions_insights_service.py service/tests/test_actions_insights.py
git commit -m "feat(service): dormant-grant and unused-role mining in actions insights"
```

---

### Task 3: Admin route

**Files:**
- Modify: `service/src/api/admin_routes.py` (add import + one endpoint after `get_activity_insights`, which is at ~line 115)

**Interfaces:**
- Consumes: `actions_insights_service.actions_insights` (Tasks 1–2).
- Produces: `GET /admin/actions/insights?days=30&workspace_id=<uuid>` → the dict from `actions_insights`, admin-cookie auth via the router-level `require_admin`.

- [ ] **Step 1: Add the route**

In `service/src/api/admin_routes.py`, add `actions_insights_service` to the existing `from src.services import (...)` block (it currently imports `admin_service, activity_service, insights_service, ...`), add `Literal` to imports (`from typing import Literal`), and add after `get_activity_insights`:

```python
@router.get("/actions/insights")
async def get_actions_insights(
    days: Literal[7, 30, 90] = Query(30),
    workspace_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await actions_insights_service.actions_insights(
        db, days=days, workspace_id=workspace_id
    )
```

No new tests: the route is a one-line passthrough — auth comes from the router-level dependency, `days` validation from `Literal` (FastAPI returns 422), and the service logic is covered by Tasks 1–2. Ponytail: a route test here would only re-test FastAPI.

- [ ] **Step 2: Verify the app imports and the suite is green**

Run: `cd service && uv run pytest -q`
Expected: full suite passes (was 444 green before this feature).

Then smoke-check the route registers: `cd service && uv run python -c "from src.main import app; print([r.path for r in app.routes if 'actions/insights' in r.path])"`
Expected: `['/admin/actions/insights']`

- [ ] **Step 3: Format and commit**

```bash
make fmt
git add service/src/api/admin_routes.py
git commit -m "feat(service): GET /admin/actions/insights endpoint"
```

---

### Task 4: Charts refactor — extract DailyStackChart

**Files:**
- Modify: `admin/src/components/charts.tsx`

**Interfaces:**
- Consumes: existing `SignInsChart` internals (bars, tooltip, sr-only table).
- Produces: `DailyStackChart({ data, days, okLabel, failLabel, emptyText })` with `data: { day: string; ok: number; fail: number }[]` — the presentational stacked daily chart. `SignInsChart` keeps its exact public API and rendering (bucketing + delegate). Also exports `ActionsTrendChart({ items, days })` with `items: { day: string; allowed: number; denied: number }[]` (fills missing days via the existing `lastNDays`, then delegates).

- [ ] **Step 1: Refactor**

In `admin/src/components/charts.tsx`:

1. Rename `SignInTooltip` to `StackTooltip` and parameterize the two label strings:

```tsx
function StackTooltip({
  active,
  label,
  payload,
  okLabel,
  failLabel,
}: SignInTipProps & { okLabel: string; failLabel: string }) {
  if (!active || !payload?.length) return null;
  const get = (key: string) => payload.find((p) => p.dataKey === key)?.value ?? 0;
  return (
    <div className="bg-popover text-popover-foreground border border-border rounded-md shadow-md px-2.5 py-1.5 text-xs whitespace-nowrap">
      <div className="text-muted-foreground mb-0.5">{label ? fmtDay(label) : ""}</div>
      <div className="flex items-center gap-1.5">
        <span className="w-2.5 h-0.5 rounded bg-chart-series" />
        <span className="font-semibold">{get("ok")}</span>
        <span className="text-muted-foreground">{okLabel}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className="w-2.5 h-0.5 rounded bg-chart-critical" />
        <span className="font-semibold">{get("fail")}</span>
        <span className="text-muted-foreground">{failLabel}</span>
      </div>
    </div>
  );
}
```

2. Extract everything in `SignInsChart` after the `useMemo` (legend, empty state, `ResponsiveContainer`, sr-only table) into:

```tsx
export function DailyStackChart({
  data,
  days,
  okLabel,
  failLabel,
  emptyText,
}: {
  data: { day: string; ok: number; fail: number }[];
  days: number;
  okLabel: string;
  failLabel: string;
  emptyText: string;
}) {
```

Identical markup to today's `SignInsChart` body, with these substitutions:
- legend texts → `{okLabel}` / `{failLabel}`
- empty-state text → `{emptyText}`
- `<Tooltip content={<SignInTooltip />} …>` → `<Tooltip content={<StackTooltip okLabel={okLabel.toLowerCase()} failLabel={failLabel.toLowerCase()} />} …>`
- `XAxis interval={6}` → `interval={Math.max(1, Math.floor(days / 5))}` (30 → 6, unchanged behavior; 7 → 1; 90 → 18)
- sr-only table caption → `` {`Daily ${okLabel.toLowerCase()} and ${failLabel.toLowerCase()}, last ${days} days`} `` and headers `<th>Day</th><th>{okLabel}</th><th>{failLabel}</th>`

3. `SignInsChart` keeps its signature and `useMemo` bucketing, then returns:

```tsx
  return (
    <DailyStackChart
      data={data}
      days={days}
      okLabel="Sign-ins"
      failLabel="Failed"
      emptyText={`No sign-in activity in the last ${days} days`}
    />
  );
```

4. Add at the end of the file:

```tsx
/** Allowed vs denied action checks per day. Fills missing days with zeros. */
export function ActionsTrendChart({
  items,
  days,
}: {
  items: { day: string; allowed: number; denied: number }[];
  days: number;
}) {
  const data = useMemo(() => {
    const byDay = new Map(lastNDays(days).map((d) => [d, { ok: 0, fail: 0 }]));
    for (const it of items) {
      const b = byDay.get(it.day);
      if (b) {
        b.ok = it.allowed;
        b.fail = it.denied;
      }
    }
    return [...byDay.entries()].map(([day, v]) => ({ day, ...v }));
  }, [items, days]);
  return (
    <DailyStackChart
      data={data}
      days={days}
      okLabel="Allowed"
      failLabel="Denied"
      emptyText={`No action checks in the last ${days} days`}
    />
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd admin && npm run build`
Expected: clean tsc + vite build, no errors.

- [ ] **Step 3: Commit**

```bash
git add admin/src/components/charts.tsx
git commit -m "refactor(admin): extract DailyStackChart, add ActionsTrendChart"
```

---

### Task 5: Usage page — types, client, component, route, nav

**Files:**
- Modify: `admin/src/types/api.ts` (after `SignInInsights`, ~line 198)
- Modify: `admin/src/api/client.ts` (after `getActivityInsights`, ~line 109)
- Create: `admin/src/pages/ActionsInsights.tsx`
- Modify: `admin/src/App.tsx` (import + route)
- Modify: `admin/src/components/Layout.tsx` (nav item)

**Interfaces:**
- Consumes: `GET /admin/actions/insights` (Task 3), `ActionsTrendChart`/`BarList` (Task 4), existing `getAllWorkspaces()` → `WorkspaceOption[] {id, name, slug}`.
- Produces: `ActionsInsightsView({ workspaceId }: { workspaceId?: string })` (used again in Task 6) and `ActionsInsightsPage()` mounted at `/usage`.

- [ ] **Step 1: Add types**

In `admin/src/types/api.ts` after `SignInInsights`:

```ts
export interface ActionCount {
  service_name: string;
  action: string;
  count: number;
}

export interface ActionUserCount {
  user_id: string;
  email: string;
  name: string;
  count: number;
}

export interface ActionTrendPoint {
  day: string; // YYYY-MM-DD
  allowed: number;
  denied: number;
}

export interface DormantGrant {
  user_id: string;
  email: string;
  name: string;
  service_name: string;
  action: string;
  role_name: string;
  workspace_id: string;
  workspace_name: string;
}

export interface UnusedRole {
  id: string;
  name: string;
  workspace_id: string;
  workspace_name: string;
  assignees: number;
  no_assignees: boolean;
}

export interface ActionsInsights {
  days: number;
  since: string;
  data_since: string | null;
  top_actions: ActionCount[];
  by_service: { service_name: string; count: number }[];
  top_users: ActionUserCount[];
  trend: ActionTrendPoint[];
  dormant_grants: { total: number; items: DormantGrant[] };
  unused_roles: UnusedRole[];
}
```

- [ ] **Step 2: Add client fn**

In `admin/src/api/client.ts` after `getActivityInsights`:

```ts
export const getActionsInsights = (days = 30, workspaceId?: string) =>
  request<import("../types/api").ActionsInsights>(
    `/admin/actions/insights?days=${days}${workspaceId ? `&workspace_id=${workspaceId}` : ""}`,
  );
```

- [ ] **Step 3: Create the page**

Create `admin/src/pages/ActionsInsights.tsx`:

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getActionsInsights, getAllWorkspaces } from "../api/client";
import { ActionsTrendChart, BarList } from "../components/charts";
import type { DormantGrant, UnusedRole } from "../types/api";

const RANGES = [7, 30, 90] as const;
type Days = (typeof RANGES)[number];

function Card({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-baseline justify-between mb-2">
        <h2 className="text-sm font-medium text-muted-foreground">{title}</h2>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="h-32 flex items-center justify-center text-sm text-muted-foreground">{text}</div>
  );
}

function DormantGrantsTable({ items, total }: { items: DormantGrant[]; total: number }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground border-b border-border">
            <th className="py-1.5 pr-3 font-medium">User</th>
            <th className="py-1.5 pr-3 font-medium">Action</th>
            <th className="py-1.5 pr-3 font-medium">Via role</th>
            <th className="py-1.5 font-medium">Workspace</th>
          </tr>
        </thead>
        <tbody>
          {items.map((g) => (
            <tr
              key={`${g.user_id}-${g.workspace_id}-${g.service_name}-${g.action}-${g.role_name}`}
              className="border-b border-border last:border-0"
            >
              <td className="py-1.5 pr-3">
                <div>{g.name}</div>
                <div className="text-xs text-muted-foreground">{g.email}</div>
              </td>
              <td className="py-1.5 pr-3 font-mono text-xs">
                {g.service_name}:{g.action}
              </td>
              <td className="py-1.5 pr-3">{g.role_name}</td>
              <td className="py-1.5">{g.workspace_name}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {total > items.length && (
        <div className="text-xs text-muted-foreground mt-2">
          Showing {items.length} of {total.toLocaleString()}
        </div>
      )}
    </div>
  );
}

function UnusedRolesTable({ roles }: { roles: UnusedRole[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-xs text-muted-foreground border-b border-border">
          <th className="py-1.5 pr-3 font-medium">Role</th>
          <th className="py-1.5 pr-3 font-medium">Workspace</th>
          <th className="py-1.5 font-medium">Assignees</th>
        </tr>
      </thead>
      <tbody>
        {roles.map((r) => (
          <tr key={r.id} className="border-b border-border last:border-0">
            <td className="py-1.5 pr-3">{r.name}</td>
            <td className="py-1.5 pr-3">{r.workspace_name}</td>
            <td className="py-1.5">
              {r.no_assignees ? (
                <span className="text-xs text-muted-foreground">none</span>
              ) : (
                r.assignees
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ActionsInsightsView({ workspaceId }: { workspaceId?: string }) {
  const [days, setDays] = useState<Days>(30);
  const { data, isLoading } = useQuery({
    queryKey: ["actions-insights", days, workspaceId ?? "all"],
    queryFn: () => getActionsInsights(days, workspaceId),
  });

  if (isLoading || !data) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-40 bg-muted rounded-lg" />
        <div className="grid grid-cols-2 gap-6">
          <div className="h-48 bg-muted rounded-lg" />
          <div className="h-48 bg-muted rounded-lg" />
        </div>
      </div>
    );
  }

  const partialData = data.data_since !== null && data.data_since > data.since;
  const windowHint = `Last ${days} days`;
  const sinceHint = partialData ? `Data since ${data.data_since}` : windowHint;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex rounded-md border border-border overflow-hidden">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setDays(r)}
              className={`px-3 py-1 text-xs ${
                days === r
                  ? "bg-primary text-primary-foreground"
                  : "bg-background text-muted-foreground hover:text-foreground"
              }`}
            >
              {r}d
            </button>
          ))}
        </div>
        {partialData && (
          <span className="text-xs text-muted-foreground">
            Usage recording began {data.data_since}
          </span>
        )}
      </div>

      <Card title="Action checks" hint={sinceHint}>
        <ActionsTrendChart items={data.trend} days={days} />
      </Card>

      <div className="grid grid-cols-2 gap-6">
        <Card title="Top actions" hint={windowHint}>
          {data.top_actions.length === 0 ? (
            <Empty text="No action checks recorded yet" />
          ) : (
            <BarList
              rows={data.top_actions.map((a) => [`${a.service_name}:${a.action}`, a.count])}
              labelWidth={180}
            />
          )}
        </Card>
        <Card title="By service" hint={windowHint}>
          {data.by_service.length === 0 ? (
            <Empty text="No action checks recorded yet" />
          ) : (
            <BarList rows={data.by_service.map((s) => [s.service_name, s.count])} />
          )}
        </Card>
      </div>

      <Card title="Most active users" hint={windowHint}>
        {data.top_users.length === 0 ? (
          <Empty text="No action checks recorded yet" />
        ) : (
          <BarList rows={data.top_users.map((u) => [u.name || u.email, u.count])} labelWidth={160} />
        )}
      </Card>

      <Card title="Dormant grants" hint={`No usage since ${partialData ? data.data_since : data.since}`}>
        {data.dormant_grants.total === 0 ? (
          <Empty text="Every grant was exercised in this window" />
        ) : (
          <DormantGrantsTable items={data.dormant_grants.items} total={data.dormant_grants.total} />
        )}
      </Card>

      <Card title="Unused roles" hint={windowHint}>
        {data.unused_roles.length === 0 ? (
          <Empty text="Every role was exercised in this window" />
        ) : (
          <UnusedRolesTable roles={data.unused_roles} />
        )}
      </Card>
    </div>
  );
}

export function ActionsInsightsPage() {
  const [workspaceId, setWorkspaceId] = useState<string>();
  const { data: workspaces } = useQuery({
    queryKey: ["workspace-options"],
    queryFn: getAllWorkspaces,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Usage</h1>
        <select
          value={workspaceId ?? ""}
          onChange={(e) => setWorkspaceId(e.target.value || undefined)}
          className="px-2 py-1.5 border border-border bg-background rounded text-xs text-foreground"
        >
          <option value="">All workspaces</option>
          {workspaces?.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
      </div>
      <ActionsInsightsView workspaceId={workspaceId} />
    </div>
  );
}
```

- [ ] **Step 4: Wire route + nav**

`admin/src/App.tsx` — add import and route (next to the `/insights` route at ~line 49):

```tsx
import { ActionsInsightsPage } from "./pages/ActionsInsights";
// …
<Route path="/usage" element={<ActionsInsightsPage />} />
```

`admin/src/components/Layout.tsx` — add `BarChart3` to the `lucide-react` import and a NAV entry after Insights (~line 35):

```tsx
{ to: "/usage", label: "Usage", Icon: BarChart3 },
```

- [ ] **Step 5: Verify build**

Run: `cd admin && npm run build`
Expected: clean build.

- [ ] **Step 6: Commit**

```bash
git add admin/src/types/api.ts admin/src/api/client.ts admin/src/pages/ActionsInsights.tsx admin/src/App.tsx admin/src/components/Layout.tsx
git commit -m "feat(admin): Usage dashboard — action analytics + role mining"
```

---

### Task 6: WorkspaceDetail Usage tab

**Files:**
- Modify: `admin/src/pages/WorkspaceDetail.tsx` (TABS at line 47, tab render at ~line 190)

**Interfaces:**
- Consumes: `ActionsInsightsView` (Task 5).

- [ ] **Step 1: Add the tab**

In `admin/src/pages/WorkspaceDetail.tsx`:

```tsx
import { ActionsInsightsView } from "./ActionsInsights";
```

Change:

```tsx
const TABS = ["Members", "Groups", "Roles", "Access", "Usage"] as const;
```

Add after the `Access` tab render:

```tsx
{tab === "Usage" && <ActionsInsightsView workspaceId={id!} />}
```

- [ ] **Step 2: Verify build**

Run: `cd admin && npm run build`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add admin/src/pages/WorkspaceDetail.tsx
git commit -m "feat(admin): Usage tab in workspace detail"
```

---

### Task 7: Full verification + manual browser pass

- [ ] **Step 1: Full service suite + lint**

```bash
cd service && uv run pytest -q
make lint
```
Expected: suite green (444 pre-existing + 7 new), lint clean.

- [ ] **Step 2: Manual browser pass**

Dev stack: service :9003 + admin :9004 (`make start` / `make admin` if not running). `action_usage` may be empty in dev; to see data, either exercise `/roles/check-action` via the demo app or insert a few rows directly:

```bash
docker exec identity-service-identity-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
INSERT INTO action_usage (day, workspace_id, user_id, service_name, action, count)
SELECT current_date - (n % 5), w.id, u.id, '\''notes'\'', '\''notes:read'\'', (n % 7) + 1
FROM generate_series(1, 20) n, (SELECT id FROM workspaces LIMIT 1) w, (SELECT id FROM users LIMIT 1) u
ON CONFLICT (day, workspace_id, user_id, service_name, action) DO UPDATE SET count = action_usage.count + 1;"'
```

Check: `/usage` renders all six cards in light + dark theme; 7/30/90 picker refetches; workspace dropdown filters; "Usage recording began …" hint shows (data is young); WorkspaceDetail → Usage tab renders scoped; dormant grants list matches expectations for the seeded workspace (roles exist from `make seed` data).

- [ ] **Step 3: Update memory handoff**

Mark `handoff-actions-dashboard.md` as built (or delete it) and note the feature in `MEMORY.md` per its conventions.
