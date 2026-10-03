# Group → Role Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let workspace groups be assigned to RBAC roles so every group member holds the role's actions, per `docs/superpowers/specs/2026-07-12-group-role-assignment-design.md`.

**Architecture:** One new table `group_roles` mirroring `user_roles` (real FKs, CASCADE). `check_action`/`get_user_actions` become a UNION of the direct path (`user_roles`) and the group path (`group_roles ⋈ group_memberships`) — the authz-token mint inherits this with zero changes. Three new admin endpoints + activity events; group mutations gain the audit events they've always lacked; admin UI RolesTab gets a Groups section.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, pytest (fake-session style — no live DB in unit tests), React + TanStack Query (admin), MkDocs.

## Global Constraints

- Work on branch `group-roles` off `main`.
- Run `make fmt` (repo root) before every commit; it must exit clean.
- Python tests: `cd service && uv run pytest` — full suite must pass at every commit.
- Unit tests use the repo's fake-session style (see `tests/test_org_admin_routes.py`, `tests/test_remove_member_cleanup.py`) — no Postgres, no Redis.
- No new dependencies anywhere (service, admin, docs).
- Service-facing API contracts (`/roles/check-action`, `/roles/user-actions`) must not change shape. No SDK changes. No JWT changes.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**Spec deviations (intentional):**
- Spec §7 mentions `docs/PLAN.md` — that file no longer exists; the three-tier description lives in `docs/guide/roles.md`, covered by Task 7.
- Spec's "authz token carries group-derived actions" test maps to Task 3's union tests: `authz_routes.py:376` calls `get_user_actions` directly, and that plumbing already has its own tests (`test_realm_authz_minting.py`). A route-level re-test would only re-test mocks.
- Spec's "workspace removal kills group-derived access" maps to the existing purge assertion in `test_remove_member_cleanup.py` (group_memberships purge) + Task 3's SQL-shape proof that the group path routes through `group_memberships`. Composite invariant, both halves tested.

---

### Task 1: `GroupRole` model + migration

**Files:**
- Modify: `service/src/models/role.py` (add `GroupRole`, add `Role.group_roles` relationship)
- Modify: `service/src/models/group.py` (add `Group.group_roles` relationship)
- Modify: `service/src/models/__init__.py:9` (export `GroupRole`)
- Create: `service/migrations/versions/c8e1a4f7d3b9_add_group_roles.py`
- Test: `service/tests/test_group_roles_service.py` (new file, starts with the model contract test)

**Interfaces:**
- Produces: `GroupRole` model — columns `id: UUID pk`, `group_id: UUID FK groups.id CASCADE`, `role_id: UUID FK roles.id CASCADE`, `assigned_by: UUID | None FK users.id SET NULL`, `assigned_at: datetime server_default now()`; unique constraint `uq_group_role (group_id, role_id)`; importable as `from src.models.role import GroupRole`.

- [ ] **Step 1: Create the branch**

```bash
cd /Users/sidx/workspace/identity-service && git checkout -b group-roles
```

- [ ] **Step 2: Write the failing model-contract test**

Create `service/tests/test_group_roles_service.py`:

```python
"""group_roles: groups as role assignees (spec 2026-07-12).

Model contract + service-layer tests in the repo's fake-session style.
The FK ondelete rules ARE the lifecycle design (group/role deletion cleans
bindings with zero purge code) — so they're asserted here, not assumed.
"""

from __future__ import annotations

from src.models.role import GroupRole


def test_group_role_table_contract():
    t = GroupRole.__table__
    assert t.name == "group_roles"
    fks = {fk.column.table.name: fk.ondelete for fk in t.foreign_keys}
    assert fks["groups"] == "CASCADE"
    assert fks["roles"] == "CASCADE"
    assert fks["users"] == "SET NULL"
    uniques = [c.name for c in t.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert "uq_group_role" in uniques
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_group_roles_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'GroupRole'`

- [ ] **Step 4: Add the model**

In `service/src/models/role.py`, append after the `UserRole` class:

```python
class GroupRole(Base):
    __tablename__ = "group_roles"
    __table_args__ = (
        UniqueConstraint("group_id", "role_id", name="uq_group_role"),
        Index("ix_group_roles_group_id", "group_id"),
        Index("ix_group_roles_role_id", "role_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    role: Mapped["Role"] = relationship(back_populates="group_roles")
    group: Mapped["Group"] = relationship(back_populates="group_roles")  # noqa: F821
```

In the same file, add to the `Role` class after the `user_roles` relationship:

```python
    group_roles: Mapped[list["GroupRole"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )
```

In `service/src/models/group.py`, add to the `Group` class after the `memberships` relationship:

```python
    group_roles: Mapped[list["GroupRole"]] = relationship(  # noqa: F821
        back_populates="group", cascade="all, delete-orphan"
    )
```

In `service/src/models/__init__.py`, change line 9 and add to `__all__`:

```python
from src.models.role import ServiceAction, Role, RoleAction, UserRole, GroupRole
```

and append `"GroupRole",` to the `__all__` list next to `"UserRole",`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_group_roles_service.py -v`
Expected: PASS

- [ ] **Step 6: Write the migration**

Create `service/migrations/versions/c8e1a4f7d3b9_add_group_roles.py`:

```python
"""add group_roles (groups as RBAC role assignees)

A group bound to a role grants the role's actions to every group member
(resolved live at check time through group_memberships). Real FKs with
CASCADE make group/role deletion clean up bindings with no purge code.

Revision ID: c8e1a4f7d3b9
Revises: b2c4d6e8f0a1
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c8e1a4f7d3b9"
down_revision: Union[str, None] = "b2c4d6e8f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "group_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("group_id", "role_id", name="uq_group_role"),
    )
    op.create_index("ix_group_roles_group_id", "group_roles", ["group_id"])
    op.create_index("ix_group_roles_role_id", "group_roles", ["role_id"])


def downgrade() -> None:
    op.drop_index("ix_group_roles_role_id", table_name="group_roles")
    op.drop_index("ix_group_roles_group_id", table_name="group_roles")
    op.drop_table("group_roles")
```

- [ ] **Step 7: Verify the full suite + lint**

Run: `cd service && uv run pytest -q && cd .. && make fmt`
Expected: all tests pass; fmt clean (fix anything it reports).

- [ ] **Step 8: Commit**

```bash
git add service/src/models/ service/migrations/versions/c8e1a4f7d3b9_add_group_roles.py service/tests/test_group_roles_service.py
git commit -m "feat(roles): GroupRole model + group_roles migration

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Service layer — assign / remove / list role groups + group_count

**Files:**
- Modify: `service/src/services/role_service.py` (imports at top; new functions after `list_role_members`; `group_count` in `list_workspace_roles`)
- Test: `service/tests/test_group_roles_service.py` (extend)

**Interfaces:**
- Consumes: `GroupRole` from Task 1.
- Produces (used by Task 4's routes):
  - `assign_group_role(db, group_id: uuid.UUID, role_id: uuid.UUID, assigned_by: uuid.UUID | None = None) -> GroupRole` — raises `ValueError("Role not found")`, `ValueError("Group not found")`, `ValueError("Group and role belong to different workspaces")`, `ValueError("Group is already assigned to this role")`.
  - `remove_group_role(db, group_id: uuid.UUID, role_id: uuid.UUID) -> None` — raises `ValueError("Group role not found")`.
  - `list_role_groups(db, role_id: uuid.UUID) -> list[dict]` — dicts with keys `group_id, name, description, member_count, assigned_at, assigned_by`.
  - `list_workspace_roles` dicts gain key `group_count`.

- [ ] **Step 1: Write the failing tests**

In `service/tests/test_group_roles_service.py`, replace the imports block (keep the module docstring) with:

```python
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from src.models.group import Group
from src.models.role import GroupRole, Role
```

then append to the file:

```python
class _Result:
    def __init__(self, scalar=None, rows=None, scalars=None):
        self._scalar = scalar
        self._rows = rows or []
        self._scalars = scalars or []

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._rows

    def scalars(self):
        parent = self

        class _S:
            def all(self_inner):
                return parent._scalars

        return _S()


class _FakeDB:
    """Queued db.get() results + queued execute() results; records adds/deletes."""

    def __init__(self, get_results=(), exec_results=(), commit_raises=None):
        self._get = list(get_results)
        self._exec = list(exec_results)
        self._commit_raises = commit_raises
        self.added = []
        self.deleted = []
        self.statements = []
        self.commits = 0

    async def get(self, _model, _pk):
        return self._get.pop(0) if self._get else None

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._exec.pop(0) if self._exec else _Result()

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1
        if self._commit_raises:
            raise self._commit_raises


def _ws_pair(same_workspace=True):
    ws_a = uuid.uuid4()
    ws_b = ws_a if same_workspace else uuid.uuid4()
    role = Role(id=uuid.uuid4(), workspace_id=ws_a, name="analyst")
    group = Group(id=uuid.uuid4(), workspace_id=ws_b, name="analysts")
    return role, group


@pytest.mark.asyncio
async def test_assign_group_role_happy_path():
    from src.services import role_service

    role, group = _ws_pair()
    db = _FakeDB(get_results=[role, group])
    gr = await role_service.assign_group_role(db, group.id, role.id, assigned_by=None)
    assert isinstance(gr, GroupRole)
    assert db.added == [gr]
    assert (gr.group_id, gr.role_id) == (group.id, role.id)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_assign_group_role_rejects_cross_workspace():
    from src.services import role_service

    role, group = _ws_pair(same_workspace=False)
    db = _FakeDB(get_results=[role, group])
    with pytest.raises(ValueError, match="different workspaces"):
        await role_service.assign_group_role(db, group.id, role.id)
    assert db.added == []


@pytest.mark.asyncio
async def test_assign_group_role_404s_on_missing_role_or_group():
    from src.services import role_service

    role, group = _ws_pair()
    with pytest.raises(ValueError, match="Role not found"):
        await role_service.assign_group_role(_FakeDB(get_results=[None]), group.id, role.id)
    with pytest.raises(ValueError, match="Group not found"):
        await role_service.assign_group_role(_FakeDB(get_results=[role, None]), group.id, role.id)


@pytest.mark.asyncio
async def test_assign_group_role_duplicate_maps_integrity_error():
    from src.services import role_service

    role, group = _ws_pair()
    dup = IntegrityError("stmt", {}, Exception("uq_group_role"))
    db = _FakeDB(get_results=[role, group], commit_raises=dup)
    with pytest.raises(ValueError, match="already assigned"):
        await role_service.assign_group_role(db, group.id, role.id)


@pytest.mark.asyncio
async def test_remove_group_role_deletes_binding():
    from src.services import role_service

    binding = GroupRole(group_id=uuid.uuid4(), role_id=uuid.uuid4())
    db = _FakeDB(exec_results=[_Result(scalar=binding)])
    await role_service.remove_group_role(db, binding.group_id, binding.role_id)
    assert db.deleted == [binding]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_remove_group_role_missing_raises():
    from src.services import role_service

    db = _FakeDB(exec_results=[_Result(scalar=None)])
    with pytest.raises(ValueError, match="Group role not found"):
        await role_service.remove_group_role(db, uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_list_role_groups_shapes_rows():
    from src.services import role_service

    role, group = _ws_pair()
    gr = GroupRole(group_id=group.id, role_id=role.id)
    db = _FakeDB(exec_results=[_Result(rows=[(gr, group, 3)])])
    rows = await role_service.list_role_groups(db, role.id)
    assert rows == [
        {
            "group_id": group.id,
            "name": "analysts",
            "description": None,
            "member_count": 3,
            "assigned_at": gr.assigned_at,
            "assigned_by": None,
        }
    ]


@pytest.mark.asyncio
async def test_list_workspace_roles_includes_group_count():
    from src.services import role_service

    role, _ = _ws_pair()
    db = _FakeDB(exec_results=[_Result(rows=[(role, 2, 5, 1)])])
    rows = await role_service.list_workspace_roles(db, role.workspace_id)
    assert rows[0]["action_count"] == 2
    assert rows[0]["member_count"] == 5
    assert rows[0]["group_count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_group_roles_service.py -v`
Expected: the new tests FAIL with `AttributeError: ... has no attribute 'assign_group_role'` (and the `group_count` KeyError); the Task 1 contract test still passes.

- [ ] **Step 3: Implement the service functions**

In `service/src/services/role_service.py`:

Replace the imports block at the top with:

```python
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.group import Group, GroupMembership
from src.models.role import GroupRole, Role, RoleAction, ServiceAction, UserRole
from src.models.user import User
```

In `list_workspace_roles`, add a third scalar subquery after `member_count` and thread it through:

```python
    group_count = (
        select(func.count(GroupRole.id))
        .where(GroupRole.role_id == Role.id)
        .correlate(Role)
        .scalar_subquery()
    )
    stmt = (
        select(
            Role,
            action_count.label("action_count"),
            member_count.label("member_count"),
            group_count.label("group_count"),
        )
        .where(Role.workspace_id == workspace_id)
        .order_by(Role.created_at)
    )
    result = await db.execute(stmt)
    return [
        {
            "id": role.id,
            "workspace_id": role.workspace_id,
            "name": role.name,
            "description": role.description,
            "created_by": role.created_by,
            "created_at": role.created_at,
            "action_count": ac,
            "member_count": mc,
            "group_count": gc,
        }
        for role, ac, mc, gc in result.all()
    ]
```

Append after `list_role_members`:

```python
async def assign_group_role(
    db: AsyncSession,
    group_id: uuid.UUID,
    role_id: uuid.UUID,
    assigned_by: uuid.UUID | None = None,
) -> GroupRole:
    role = await db.get(Role, role_id)
    if not role:
        raise ValueError("Role not found")
    group = await db.get(Group, group_id)
    if not group:
        raise ValueError("Group not found")
    # Groups are workspace-scoped and group members are guaranteed workspace
    # members (group_service.add_member guard + remove_member purge), so this
    # is the only scope check a group binding needs.
    if group.workspace_id != role.workspace_id:
        raise ValueError("Group and role belong to different workspaces")
    gr = GroupRole(group_id=group_id, role_id=role_id, assigned_by=assigned_by)
    db.add(gr)
    try:
        await db.commit()
    except IntegrityError:
        raise ValueError("Group is already assigned to this role") from None
    return gr


async def remove_group_role(
    db: AsyncSession,
    group_id: uuid.UUID,
    role_id: uuid.UUID,
) -> None:
    stmt = select(GroupRole).where(
        GroupRole.group_id == group_id,
        GroupRole.role_id == role_id,
    )
    result = await db.execute(stmt)
    gr = result.scalar_one_or_none()
    if not gr:
        raise ValueError("Group role not found")
    await db.delete(gr)
    await db.commit()


async def list_role_groups(
    db: AsyncSession,
    role_id: uuid.UUID,
) -> list[dict]:
    member_count = (
        select(func.count(GroupMembership.id))
        .where(GroupMembership.group_id == Group.id)
        .correlate(Group)
        .scalar_subquery()
    )
    stmt = (
        select(GroupRole, Group, member_count.label("member_count"))
        .join(Group, GroupRole.group_id == Group.id)
        .where(GroupRole.role_id == role_id)
        .order_by(GroupRole.assigned_at)
    )
    result = await db.execute(stmt)
    return [
        {
            "group_id": gr.group_id,
            "name": g.name,
            "description": g.description,
            "member_count": mc,
            "assigned_at": gr.assigned_at,
            "assigned_by": gr.assigned_by,
        }
        for gr, g, mc in result.all()
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_group_roles_service.py -v`
Expected: all PASS

- [ ] **Step 5: Full suite + lint, then commit**

```bash
cd service && uv run pytest -q && cd .. && make fmt
git add service/src/services/role_service.py service/tests/test_group_roles_service.py
git commit -m "feat(roles): assign/remove/list group-role bindings + group_count

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Check-path union — `check_action` / `get_user_actions` resolve via groups

**Files:**
- Modify: `service/src/services/role_service.py:73-116` (replace `check_action` and `get_user_actions` with a shared union builder)
- Test: `service/tests/test_check_action_group_union.py` (new)

**Interfaces:**
- Consumes: `GroupRole`, `GroupMembership` (already imported in Task 2).
- Produces: `check_action` / `get_user_actions` with **unchanged signatures and return types** — `(bool, list[str])` and `list[str]`. `authz_routes.py:376` and `role_routes.py` need no changes.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_check_action_group_union.py`:

```python
"""check_action/get_user_actions must resolve roles granted via groups.

Repo fake-session style: assert the compiled SQL unions the direct path
(user_roles) with the group path (group_roles ⋈ group_memberships), and that
the union routes through group_memberships — which is what makes the
workspace-removal purge (test_remove_member_cleanup.py) kill group-derived
access. UNION semantics (dedup) are a DB concern, not re-tested here.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects import postgresql

from src.services import role_service


class _Result:
    def __init__(self, scalars):
        self._scalars = scalars

    def scalars(self):
        parent = self

        class _S:
            def all(self_inner):
                return parent._scalars

        return _S()


class _RecordingDB:
    def __init__(self, scalars):
        self._scalars = scalars
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._scalars)


def _compiled(db):
    return str(db.statements[0].compile(dialect=postgresql.dialect())).lower()


@pytest.mark.asyncio
async def test_check_action_unions_direct_and_group_paths():
    db = _RecordingDB(scalars=["analyst"])
    allowed, roles = await role_service.check_action(
        db,
        user_id=uuid.uuid4(),
        service_name="notes",
        action="notes:read",
        workspace_id=uuid.uuid4(),
    )
    assert (allowed, roles) == (True, ["analyst"])
    sql = _compiled(db)
    assert "union" in sql
    assert "user_roles" in sql
    assert "group_roles" in sql
    assert "group_memberships" in sql


@pytest.mark.asyncio
async def test_check_action_denies_when_no_path_grants():
    db = _RecordingDB(scalars=[])
    allowed, roles = await role_service.check_action(
        db,
        user_id=uuid.uuid4(),
        service_name="notes",
        action="notes:read",
        workspace_id=uuid.uuid4(),
    )
    assert (allowed, roles) == (False, [])


@pytest.mark.asyncio
async def test_get_user_actions_unions_direct_and_group_paths():
    db = _RecordingDB(scalars=["notes:read", "notes:write"])
    actions = await role_service.get_user_actions(
        db,
        user_id=uuid.uuid4(),
        service_name="notes",
        workspace_id=uuid.uuid4(),
    )
    assert actions == ["notes:read", "notes:write"]
    sql = _compiled(db)
    assert "union" in sql
    assert "user_roles" in sql
    assert "group_roles" in sql
    assert "group_memberships" in sql


@pytest.mark.asyncio
async def test_both_paths_filter_by_workspace_service_and_user():
    """Every branch of the union must scope by workspace + service + user —
    a branch missing one of these is a cross-workspace/service leak."""
    db = _RecordingDB(scalars=[])
    await role_service.check_action(
        db,
        user_id=uuid.uuid4(),
        service_name="notes",
        action="notes:read",
        workspace_id=uuid.uuid4(),
    )
    sql = _compiled(db)
    assert sql.count("workspace_id") >= 2
    assert sql.count("service_name") >= 2
    assert sql.count("user_id") >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_check_action_group_union.py -v`
Expected: FAIL — compiled SQL has no `union`/`group_roles` (first, third, fourth tests).

- [ ] **Step 3: Replace the two functions with a shared union builder**

In `service/src/services/role_service.py`, replace the entire `check_action` and `get_user_actions` functions (currently lines 73–116) with:

```python
def _granted_stmt(
    col,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    service_name: str,
    action: str | None = None,
):
    """UNION of the two grant paths: direct (user_roles) and via group
    (group_roles ⋈ group_memberships). UNION dedups, so a role granted both
    ways appears once. Group members are always workspace members
    (group_service guards + remove_member purge), so no membership re-join."""

    def _scoped(stmt):
        stmt = stmt.where(
            Role.workspace_id == workspace_id,
            ServiceAction.service_name == service_name,
        )
        if action is not None:
            stmt = stmt.where(ServiceAction.action == action)
        return stmt

    direct = _scoped(
        select(col)
        .join(UserRole, UserRole.role_id == Role.id)
        .join(RoleAction, RoleAction.role_id == Role.id)
        .join(ServiceAction, RoleAction.service_action_id == ServiceAction.id)
        .where(UserRole.user_id == user_id)
    )
    via_group = _scoped(
        select(col)
        .join(GroupRole, GroupRole.role_id == Role.id)
        .join(GroupMembership, GroupMembership.group_id == GroupRole.group_id)
        .join(RoleAction, RoleAction.role_id == Role.id)
        .join(ServiceAction, RoleAction.service_action_id == ServiceAction.id)
        .where(GroupMembership.user_id == user_id)
    )
    return direct.union(via_group)


async def check_action(
    db: AsyncSession,
    user_id: uuid.UUID,
    service_name: str,
    action: str,
    workspace_id: uuid.UUID,
) -> tuple[bool, list[str]]:
    stmt = _granted_stmt(Role.name, user_id, workspace_id, service_name, action)
    result = await db.execute(stmt)
    roles = list(result.scalars().all())
    return (len(roles) > 0, roles)


async def get_user_actions(
    db: AsyncSession,
    user_id: uuid.UUID,
    service_name: str,
    workspace_id: uuid.UUID,
) -> list[str]:
    stmt = _granted_stmt(ServiceAction.action, user_id, workspace_id, service_name)
    result = await db.execute(stmt)
    return list(result.scalars().all())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_check_action_group_union.py tests/test_group_roles_service.py tests/test_role_routes_authz.py -v`
Expected: all PASS (the role_routes tests monkeypatch these functions — they prove signatures didn't drift).

- [ ] **Step 5: Full suite + lint, then commit**

```bash
cd service && uv run pytest -q && cd .. && make fmt
git add service/src/services/role_service.py service/tests/test_check_action_group_union.py
git commit -m "feat(roles): resolve check_action/get_user_actions via groups (union)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Schemas + admin endpoints + role_group activity events

**Files:**
- Modify: `service/src/schemas/role.py` (add `RoleGroupResponse`; add `group_count` to `RoleResponse`)
- Modify: `service/src/api/admin_routes.py` (import `RoleGroupResponse` at line 62 block; three endpoints after `remove_role_member`, i.e. after line ~1177)
- Test: `service/tests/test_role_group_admin_routes.py` (new)

**Interfaces:**
- Consumes: `assign_group_role` / `remove_group_role` / `list_role_groups` from Task 2 (exact signatures in Task 2's Produces block).
- Produces: `GET /admin/roles/{role_id}/groups`, `POST /admin/roles/{role_id}/groups/{group_id}` (201), `DELETE /admin/roles/{role_id}/groups/{group_id}` (204); activity actions `role_group_added` / `role_group_removed`; `RoleResponse.group_count: int = 0` (Task 6's UI reads it).

- [ ] **Step 1: Write the failing route tests**

Create `service/tests/test_role_group_admin_routes.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_role_group_admin_routes.py -v`
Expected: FAIL with 404/405 responses (routes don't exist yet).

- [ ] **Step 3: Add schema fields**

In `service/src/schemas/role.py`, add `group_count` to `RoleResponse` after `member_count`:

```python
    group_count: int = 0
```

and append after `RoleMemberResponse`:

```python
class RoleGroupResponse(BaseModel):
    group_id: uuid.UUID
    name: str
    description: str | None
    member_count: int
    assigned_at: datetime
    assigned_by: uuid.UUID | None
```

- [ ] **Step 4: Add the admin routes**

In `service/src/api/admin_routes.py`, add `RoleGroupResponse` to the `from src.schemas.role import (...)` block (line 62), then append after the `remove_role_member` handler (after line ~1177):

```python
@router.get("/roles/{role_id}/groups", response_model=list[RoleGroupResponse])
async def list_role_groups(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await role_service.list_role_groups(db, role_id)


def _group_role_error(e: ValueError) -> HTTPException:
    detail = str(e)
    if "not found" in detail.lower():
        return HTTPException(status_code=404, detail=detail)
    if "already assigned" in detail.lower():
        return HTTPException(status_code=409, detail=detail)
    return HTTPException(status_code=400, detail=detail)


@router.post("/roles/{role_id}/groups/{group_id}", status_code=201)
async def assign_role_group(
    role_id: uuid.UUID,
    group_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await role_service.assign_group_role(
            db, group_id, role_id, assigned_by=uuid.UUID(admin["sub"])
        )
    except ValueError as e:
        raise _group_role_error(e)

    await activity_service.log_activity(
        db,
        action="role_group_added",
        target_type="group",
        target_id=group_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"role_id": str(role_id)},
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/roles/{role_id}/groups/{group_id}", status_code=204)
async def remove_role_group(
    role_id: uuid.UUID,
    group_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await role_service.remove_group_role(db, group_id, role_id)
    except ValueError as e:
        raise _group_role_error(e)

    await activity_service.log_activity(
        db,
        action="role_group_removed",
        target_type="group",
        target_id=group_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"role_id": str(role_id)},
    )
    await db.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_role_group_admin_routes.py -v`
Expected: all PASS

- [ ] **Step 6: Full suite + lint, then commit**

```bash
cd service && uv run pytest -q && cd .. && make fmt
git add service/src/schemas/role.py service/src/api/admin_routes.py service/tests/test_role_group_admin_routes.py
git commit -m "feat(admin): role↔group binding endpoints + audit events

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Audit events for group mutations (pre-existing gap)

**Files:**
- Modify: `service/src/api/group_routes.py` (imports; four handlers)
- Test: `service/tests/test_group_audit_events.py` (new)

**Interfaces:**
- Consumes: `activity_service.log_activity(db, action, target_type, target_id, actor_id=None, workspace_id=None, detail=None)`.
- Produces: activity actions `group_created`, `group_deleted`, `group_member_added`, `group_member_removed` (all carry `workspace_id`).

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_group_audit_events.py`:

```python
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
    resp = _build_app().post(f"/workspaces/{WS_ID}/groups/{GROUP_ID}/members/{MEMBER_ID}")
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
    resp = _build_app().post(f"/workspaces/{WS_ID}/groups/{GROUP_ID}/members/{MEMBER_ID}")
    assert resp.status_code == 404
    assert activity == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_group_audit_events.py -v`
Expected: FAIL — mutations return success but `activity` list is empty.

- [ ] **Step 3: Add the audit calls**

In `service/src/api/group_routes.py`:

Add to the imports:

```python
from src.services import activity_service, group_service
```

(replacing the existing `from src.services import group_service`).

In `create_group`, replace the `try` block's return with log-then-return:

```python
    try:
        group = await group_service.create_group(
            db,
            workspace_id=workspace_id,
            name=body.name,
            created_by=user.user_id,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await activity_service.log_activity(
        db,
        action="group_created",
        target_type="group",
        target_id=group.id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
    )
    await db.commit()
    return group
```

In `delete_group`, after the `try/except` around the service call:

```python
    try:
        await group_service.delete_group(db, group_id, workspace_id)
    except ValueError as e:
        raise _to_http(e)
    await activity_service.log_activity(
        db,
        action="group_deleted",
        target_type="group",
        target_id=group_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
    )
    await db.commit()
```

In `add_group_member`, between the `try/except` and `return {"status": "ok"}`:

```python
    await activity_service.log_activity(
        db,
        action="group_member_added",
        target_type="user",
        target_id=member_user_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
        detail={"group_id": str(group_id)},
    )
    await db.commit()
```

In `remove_group_member`, after the `try/except`:

```python
    await activity_service.log_activity(
        db,
        action="group_member_removed",
        target_type="user",
        target_id=member_user_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
        detail={"group_id": str(group_id)},
    )
    await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_group_audit_events.py -v`
Expected: all PASS

- [ ] **Step 5: Full suite + lint, then commit**

```bash
cd service && uv run pytest -q && cd .. && make fmt
git add service/src/api/group_routes.py service/tests/test_group_audit_events.py
git commit -m "feat(groups): audit events for group create/delete/membership

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Admin UI — Groups section in RolesTab

**Files:**
- Modify: `admin/src/types/api.ts` (add `RoleGroup` after `RoleMember` at line 127; add `group_count` to `CustomRole` at line 116)
- Modify: `admin/src/api/client.ts` (three functions after `removeRoleMember` at line 327; import `RoleGroup` in the types import at the top of the file)
- Modify: `admin/src/pages/WorkspaceDetail.tsx` (imports at line 6-35; `RolesTab` at line 583+)

**Interfaces:**
- Consumes: Task 4's endpoints and `RoleResponse.group_count`.
- Produces: admin UI only — nothing downstream consumes this.

- [ ] **Step 1: Add the type**

In `admin/src/types/api.ts`, add `group_count: number;` to `CustomRole` after `member_count: number;`, and append after the `RoleMember` interface:

```typescript
export interface RoleGroup {
  group_id: string;
  name: string;
  description: string | null;
  member_count: number;
  assigned_at: string;
  assigned_by: string | null;
}
```

- [ ] **Step 2: Add the client functions**

In `admin/src/api/client.ts`, add `RoleGroup` to the existing `import type { ... } from "../types/api"` list, then append after `removeRoleMember`:

```typescript
export const getRoleGroups = (roleId: string) =>
  request<RoleGroup[]>(`/admin/roles/${roleId}/groups`);

export const addRoleGroup = (roleId: string, groupId: string) =>
  request(`/admin/roles/${roleId}/groups/${groupId}`, { method: "POST" });

export const removeRoleGroup = (roleId: string, groupId: string) =>
  request(`/admin/roles/${roleId}/groups/${groupId}`, { method: "DELETE" });
```

- [ ] **Step 3: Wire the RolesTab**

In `admin/src/pages/WorkspaceDetail.tsx`:

1. Add `addRoleGroup,` `getRoleGroups,` `removeRoleGroup,` to the `../api/client` import list (keep it alphabetized).

2. In `RolesTab`, add state after `addMemberEmail` (line ~590):

```typescript
  const [selectedGroupId, setSelectedGroupId] = useState("");
```

3. Add queries after the `roleMembers` query (line ~617). The `workspace-groups` key is shared with `GroupsTab`, so the cache is reused:

```typescript
  const { data: groups = [] } = useQuery({
    queryKey: ["workspace-groups", workspaceId],
    queryFn: () => getWorkspaceGroups(workspaceId),
  });

  const { data: roleGroups = [] } = useQuery({
    queryKey: ["role-groups", expandedRole],
    queryFn: () => getRoleGroups(expandedRole!),
    enabled: !!expandedRole,
  });
```

4. Add mutations after `removeMemberMut` (line ~683):

```typescript
  const addGroup = useMutation({
    mutationFn: (groupId: string) => addRoleGroup(expandedRole!, groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["role-groups", expandedRole] });
      queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
      setSelectedGroupId("");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const removeGroupMut = useMutation({
    mutationFn: (groupId: string) => removeRoleGroup(expandedRole!, groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["role-groups", expandedRole] });
      queryClient.invalidateQueries({ queryKey: ["workspace-roles", workspaceId] });
    },
    onError: (e) => toast.error((e as Error).message),
  });
```

5. Update the counts line (line ~714-716):

```tsx
                <div className="text-xs text-muted-foreground mt-0.5">
                  {r.action_count} actions · {r.member_count} members · {r.group_count} groups
                </div>
```

6. Add the Groups section inside the expanded-role detail, after the Members section's closing `</div>` (line ~839, still inside the `px-4 pb-3 space-y-4` container):

```tsx
                {/* Groups section */}
                <div>
                  <div className="text-xs font-medium text-muted-foreground pb-1">Groups</div>
                  <div className="flex items-center gap-2">
                    <select
                      value={selectedGroupId}
                      onChange={(e) => setSelectedGroupId(e.target.value)}
                      className="flex-1 rounded border border-border bg-background px-2 py-1.5 text-xs text-foreground"
                    >
                      <option value="">Select group to add...</option>
                      {groups
                        .filter((g) => !roleGroups.some((rg) => rg.group_id === g.id))
                        .map((g) => (
                          <option key={g.id} value={g.id}>
                            {g.name}
                          </option>
                        ))}
                    </select>
                    <Button
                      size="sm"
                      onClick={() => { if (selectedGroupId) addGroup.mutate(selectedGroupId); }}
                      disabled={!selectedGroupId || addGroup.isPending}
                    >
                      Add
                    </Button>
                  </div>
                  <div className="divide-y divide-border mt-1">
                    {roleGroups.map((rg) => (
                      <div key={rg.group_id} className="flex items-center justify-between py-2">
                        <div className="text-sm">
                          <span className="text-foreground">{rg.name}</span>
                          <span className="text-muted-foreground ml-2 text-xs">
                            {rg.member_count} member{rg.member_count === 1 ? "" : "s"}
                          </span>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => removeGroupMut.mutate(rg.group_id)}
                          className="text-destructive hover:text-destructive"
                        >
                          Remove
                        </Button>
                      </div>
                    ))}
                    {roleGroups.length === 0 && (
                      <div className="py-2 text-xs text-muted-foreground">No groups assigned</div>
                    )}
                  </div>
                </div>
```

- [ ] **Step 4: Verify build + lint**

Run: `cd admin && npm run build && npm run lint`
Expected: `tsc -b` clean, vite build succeeds, eslint clean.

- [ ] **Step 5: Commit**

```bash
git add admin/src/types/api.ts admin/src/api/client.ts admin/src/pages/WorkspaceDetail.tsx
git commit -m "feat(admin-ui): groups section in role detail + group counts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Docs

**Files:**
- Modify: `docs/guide/roles.md` (section 3, line ~98)
- Modify: `docs/api/roles.md` (check-action description, line ~60)

**Interfaces:** none — prose only.

- [ ] **Step 1: Update the guide**

In `docs/guide/roles.md`, find the section:

```
### 3. Assign Users to Roles (Admin)

```
POST /admin/roles/{role_id}/members/{user_id}
```
```

Change the heading to `### 3. Assign Users or Groups to Roles (Admin)` and extend the section to:

```markdown
### 3. Assign Users or Groups to Roles (Admin)

```
POST /admin/roles/{role_id}/members/{user_id}
POST /admin/roles/{role_id}/groups/{group_id}
```

Assigning a **group** grants the role's actions to every member of the group,
for as long as they are in it. Groups are flat (no nesting) and
workspace-scoped; a role and its groups must belong to the same workspace.
Group-derived grants resolve live: adding someone to the group grants the
actions on their next check, removing them (or deleting the group) revokes.

!!! note "Delegation boundary"
    Binding a group to a role is admin-panel-only, but *group membership* is
    managed by workspace admins/owners. Once a group is bound, workspace
    admins effectively control who holds those actions — they choose who is
    on the team, never what the team can do. This mirrors how group shares
    already work in entity ACLs.
```

- [ ] **Step 2: Update the API reference**

In `docs/api/roles.md`, in the `## POST /roles/check-action` section, change the line:

```
Checks whether the user can perform a specific action in a workspace. Returns the roles that grant the action.
```

to:

```
Checks whether the user can perform a specific action in a workspace. Returns the roles that grant the action — whether assigned to the user directly or via a group the user belongs to.
```

- [ ] **Step 3: Verify strict docs build**

Run: `cd /Users/sidx/workspace/identity-service && uv run --extra docs mkdocs build --strict`
Expected: build succeeds with no warnings (strict mode fails on any).

- [ ] **Step 4: Commit**

```bash
git add docs/guide/roles.md docs/api/roles.md
git commit -m "docs(roles): group→role assignment guide + API notes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Final verification

**Files:** none (verification only).

- [ ] **Step 1: Full gates**

```bash
cd /Users/sidx/workspace/identity-service/service && uv run pytest -q
cd /Users/sidx/workspace/identity-service/sdk && uv run pytest -q
cd /Users/sidx/workspace/identity-service && make lint
cd /Users/sidx/workspace/identity-service/admin && npm run build && npm run lint
cd /Users/sidx/workspace/identity-service && uv run --extra docs mkdocs build --strict
```

Expected: everything green. (SDK suite is untouched by this feature — running it proves that.)

- [ ] **Step 2: Live smoke (if containers are up)**

If `make start` infrastructure is available: start the service (`make start`), confirm the migration applies (startup runs `alembic upgrade head`), then `make seed` and exercise the new admin endpoints with the admin panel (`make admin`): create a group, bind it to a role with actions, confirm the role row shows the group and counts. This is optional if no local infra; CI + review cover the rest.

- [ ] **Step 3: Done — hand off**

Use superpowers:finishing-a-development-branch to decide merge/PR.
