"""group_roles: groups as role assignees (spec 2026-07-12).

Model contract + service-layer tests in the repo's fake-session style.
The FK ondelete rules ARE the lifecycle design (group/role deletion cleans
bindings with zero purge code) — so they're asserted here, not assumed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from src.models.group import Group
from src.models.role import GroupRole, Role


def test_group_role_table_contract():
    t = GroupRole.__table__
    assert t.name == "group_roles"
    fks = {fk.column.table.name: fk.ondelete for fk in t.foreign_keys}
    assert fks["groups"] == "CASCADE"
    assert fks["roles"] == "CASCADE"
    assert fks["users"] == "SET NULL"
    uniques = [
        c.name for c in t.constraints if c.__class__.__name__ == "UniqueConstraint"
    ]
    assert "uq_group_role" in uniques


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
        await role_service.assign_group_role(
            _FakeDB(get_results=[None]), group.id, role.id
        )
    with pytest.raises(ValueError, match="Group not found"):
        await role_service.assign_group_role(
            _FakeDB(get_results=[role, None]), group.id, role.id
        )


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
