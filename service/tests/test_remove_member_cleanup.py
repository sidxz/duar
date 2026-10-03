"""Removing a workspace member must purge their workspace-scoped group
memberships and RBAC role assignments in the same transaction.

Neither group_memberships nor user_roles is FK-tied to workspace_memberships,
and role_service.check_action never re-joins WorkspaceMembership — so stale
rows silently reinstate old privileges the moment the user is re-invited
(even as a plain viewer).
"""

import uuid

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Delete

from src.models.workspace import WorkspaceMembership
from src.services import workspace_service


class _Session:
    """Serves the membership select; records DELETE statements and ORM deletes."""

    def __init__(self, membership):
        self._membership = membership
        self.delete_sql: list[str] = []
        self.orm_deleted = []

    async def execute(self, stmt):
        if isinstance(stmt, Delete):
            self.delete_sql.append(
                str(stmt.compile(dialect=postgresql.dialect())).lower()
            )
            return None
        membership = self._membership

        class _Result:
            def scalar_one_or_none(self):
                return membership

        return _Result()

    async def delete(self, obj):
        self.orm_deleted.append(obj)

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_remove_member_purges_workspace_scoped_groups_and_roles(monkeypatch):
    async def _noop(_uid):
        return None

    monkeypatch.setattr(
        workspace_service.token_service, "revoke_all_user_tokens", _noop
    )

    workspace_id, user_id = uuid.uuid4(), uuid.uuid4()
    membership = WorkspaceMembership(
        workspace_id=workspace_id, user_id=user_id, role="editor"
    )
    session = _Session(membership)

    await workspace_service.remove_member(
        session, workspace_id, user_id, actor_role="admin"
    )

    assert session.orm_deleted == [membership]
    group_deletes = [s for s in session.delete_sql if "from group_memberships" in s]
    role_deletes = [s for s in session.delete_sql if "from user_roles" in s]
    assert group_deletes, "must purge the user's group memberships in this workspace"
    assert role_deletes, "must purge the user's role assignments in this workspace"
    # Scoped: this user AND this workspace only — not a global purge of the user.
    assert "user_id" in group_deletes[0] and "workspace_id" in group_deletes[0]
    assert "user_id" in role_deletes[0] and "workspace_id" in role_deletes[0]
