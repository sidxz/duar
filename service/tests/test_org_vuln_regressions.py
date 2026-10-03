"""Regression tests for the org-feature security fixes.

V7 — CSV import must NOT persist an orphan user when the allowed-orgs gate
     rejects the row (and must not over-count users_created).
V9 — refresh-token rotation must revoke the whole family if ANY error occurs
     after the old token was consumed (fail closed).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.user import User
from src.models.workspace import WorkspaceMembership
from src.services import admin_service, auth_service


# ── V7: CSV import orphan-user leak ──────────────────────────────────────────


class _Result:
    def __init__(self, *, scalar=None, rows=None, scalars=None):
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


class _ImportDB:
    """Serves queued execute() results; records added objects and commits."""

    def __init__(self, execute_results):
        self._exec = list(execute_results)
        self.added = []
        self.committed = False

    async def execute(self, _stmt):
        return self._exec.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_import_rejected_row_creates_no_orphan_user():
    """A row rejected by the allowed-orgs gate must leave NO User behind and must
    not inflate users_created — the user is created only after the gate passes."""
    workspace = MagicMock()
    workspace.id = uuid.uuid4()
    workspace.slug = "secure-ws"
    allowed_org = uuid.uuid4()  # workspace restricted to a different org

    rows = [
        {
            "email": "alice@gmail.com",  # unclaimed domain -> resolves to public
            "name": "Alice",
            "workspace_slug": "secure-ws",
            "role": "viewer",
            "error": None,
        }
    ]

    public_org = MagicMock()
    public_org.id = uuid.uuid4()
    public_org.is_public = True
    public_org.enabled = True

    # execute order: workspace lookup, user lookup (none), resolve_organization
    # domains query (no rows), resolve_organization public fallback, then
    # workspace_allows_org (restricted to allowed_org, so the public org fails).
    db = _ImportDB(
        execute_results=[
            _Result(scalar=workspace),  # select Workspace
            _Result(scalar=None),  # select User -> not found
            _Result(rows=[]),  # resolve_organization: no domain rows
            _Result(scalar=public_org),  # resolve_organization: public fallback
            _Result(scalars=[allowed_org]),  # workspace_allows_org -> denied
        ]
    )

    result = await admin_service.execute_import(db, rows)

    assert result["users_created"] == 0, "rejected row must not count as created"
    assert result["memberships_added"] == 0
    assert any("alice@gmail.com" in e for e in result["errors"])
    assert not [o for o in db.added if isinstance(o, User)], (
        "a rejected import row left an orphan User in the session"
    )
    assert not [o for o in db.added if isinstance(o, WorkspaceMembership)]
    assert db.committed is True  # commit still runs (for any valid rows)


# ── V9: rotation must revoke the family on any post-consume failure ───────────


@pytest.mark.asyncio
async def test_rotate_revokes_family_on_unexpected_error():
    """If an unexpected error happens AFTER consume_refresh_token (which is a
    one-time getdel), the whole family must be revoked so a partially-rotated
    session cannot linger — the old token is already spent."""
    user = MagicMock()
    user.id = uuid.uuid4()
    user.is_active = True
    user.organization_id = None

    family_id = str(uuid.uuid4())
    workspace_id = uuid.uuid4()

    db = MagicMock()
    db.get = AsyncMock(return_value=user)
    # The membership query blows up with an unexpected (non-ValueError) error.
    db.execute = AsyncMock(side_effect=RuntimeError("database unavailable"))

    revoked: list[str] = []

    async def fake_consume(jti):
        return (user.id, family_id, workspace_id, None)

    async def fake_revoke(fid):
        revoked.append(fid)

    from src.auth.jwt import create_refresh_token

    refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)

    with (
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=fake_consume,
        ),
        patch(
            "src.services.auth_service.token_service.revoke_token_family",
            new=fake_revoke,
        ),
    ):
        with pytest.raises(RuntimeError):
            await auth_service.rotate_refresh_token(db, refresh_token)

    assert revoked == [family_id], (
        "an unexpected error after consume_refresh_token must revoke the family; "
        "otherwise the old token is spent but the family lingers un-revoked"
    )
