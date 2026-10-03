"""invite_member must reject users whose org is not allowed by the workspace."""

import uuid

import pytest

from src.models.user import User
from src.services import workspace_service


class _ScalarResult:
    def __init__(self, value=None, scalars=None):
        self._value = value
        self._scalars = scalars or []

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        parent = self

        class _S:
            def all(self_inner):
                return parent._scalars

        return _S()


class _Org:
    def __init__(self, org_id, *, enabled=True, is_public=False):
        self.id = org_id
        self.enabled = enabled
        self.is_public = is_public


class _FakeSession:
    def __init__(self, exec_results, get_map=None):
        self._exec = list(exec_results)
        self._get = get_map or {}
        self.added = []
        self.committed = False

    async def execute(self, _stmt):
        return self._exec.pop(0)

    async def get(self, _model, pk):
        return self._get.get(pk)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _user_with_org(org_id):
    u = User(email="x@tamu.edu", name="X")
    u.id = uuid.uuid4()
    u.organization_id = org_id
    return u


@pytest.mark.asyncio
async def test_invite_rejected_when_org_not_allowed():
    org_id = uuid.uuid4()
    user = _user_with_org(org_id)
    allowed_org = uuid.uuid4()  # different from the user's org
    session = _FakeSession(
        exec_results=[
            _ScalarResult(value=user),  # select User by email
            _ScalarResult(value=None),  # existing-membership check
            _ScalarResult(scalars=[allowed_org]),  # workspace_allows_org query
        ],
        get_map={org_id: _Org(org_id)},  # effective_org -> enabled real org
    )
    with pytest.raises(ValueError, match="organization is not permitted"):
        await workspace_service.invite_member(
            session, uuid.uuid4(), "x@tamu.edu", role="viewer"
        )
    assert session.committed is False


@pytest.mark.asyncio
async def test_invite_allowed_when_workspace_open():
    org_id = uuid.uuid4()
    user = _user_with_org(org_id)
    session = _FakeSession(
        exec_results=[
            _ScalarResult(value=user),  # select User by email
            _ScalarResult(value=None),  # existing-membership check
            _ScalarResult(scalars=[]),  # no allowed-org rows => open
        ],
        get_map={org_id: _Org(org_id)},  # effective_org -> enabled real org
    )
    membership = await workspace_service.invite_member(
        session, uuid.uuid4(), "x@tamu.edu", role="viewer"
    )
    assert membership.user_id == user.id
    assert session.committed is True


class _SessionWithGet:
    """Like _FakeSession but serves get() dispatched BY MODEL. A single FIFO
    would hand its one queued User to effective_org's Organization lookup too
    (returning None), silently rerouting these tests through the org=None
    branch instead of the real "user's org vs allow-list" comparison."""

    def __init__(self, *, get_map=None, exec_results=None):
        self._get_map = get_map or {}
        self._exec = list(exec_results or [])
        self.added = []
        self.flushed = False
        self.committed = False

    async def get(self, model, _pk):
        return self._get_map.get(model)

    async def execute(self, _stmt):
        return self._exec.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_admin_add_user_rejected_when_org_not_allowed():
    # The admin "add user to workspace" path must enforce the same allowed-orgs
    # gate as invite, so it can't create a membership the user could never redeem.
    from src.models.organization import Organization
    from src.models.workspace import WorkspaceMembership
    from src.services import admin_service

    user = _user_with_org(uuid.uuid4())
    allowed = uuid.uuid4()  # workspace restricted to a different org
    session = _SessionWithGet(
        get_map={
            User: user,  # db.get(User)
            Organization: _Org(user.organization_id),  # effective_org lookup
        },
        exec_results=[_ScalarResult(scalars=[allowed])],  # workspace_allows_org
    )
    with pytest.raises(ValueError, match="organization is not permitted"):
        await admin_service.add_user_to_workspace(
            session, user.id, uuid.uuid4(), "viewer"
        )
    assert session.committed is False
    assert not [o for o in session.added if isinstance(o, WorkspaceMembership)]


@pytest.mark.asyncio
async def test_admin_add_user_allowed_when_org_in_allowlist():
    # Companion positive case: proves the user's REAL org flows through the
    # gate (a mock returning org=None would be rejected here, not allowed).
    from src.models.organization import Organization
    from src.services import admin_service

    user = _user_with_org(uuid.uuid4())
    session = _SessionWithGet(
        get_map={
            User: user,
            Organization: _Org(user.organization_id),
        },
        exec_results=[_ScalarResult(scalars=[user.organization_id])],
    )
    membership = await admin_service.add_user_to_workspace(
        session, user.id, uuid.uuid4(), "viewer"
    )
    assert membership.user_id == user.id
    assert session.committed is True
