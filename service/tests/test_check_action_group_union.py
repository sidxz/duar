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
    # Pin the group-path join condition: a mangled join here would grant/deny
    # the wrong users while every table/filter assertion still passes.
    assert "group_memberships.group_id = group_roles.group_id" in sql


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
    # Each scoped branch contributes 2 hits per filter (column identifier +
    # bind param), so both-branches-filtered means >= 4; a single branch
    # dropping a filter falls to 2 and fails.
    assert sql.count("workspace_id") >= 4
    assert sql.count("service_name") >= 4
    assert sql.count("user_id") >= 4
    # The user filter uses a different column per branch — pin both.
    assert "user_roles.user_id" in sql
    assert "group_memberships.user_id" in sql
