"""org_admin_service CRUD + guard logic, via fake sessions (no DB)."""

import uuid

import pytest

from src.models.organization import WorkspaceAllowedOrganization
from src.services import org_admin_service as svc


class _Result:
    def __init__(self, *, scalar=None, scalars=None, rows=None):
        self._scalar = scalar
        self._scalars = scalars or []
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
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
    """Serves queued execute() results; records added/deleted objects."""

    def __init__(self, *, execute_results=None, get_results=None):
        self._exec = list(execute_results or [])
        self._get = list(get_results or [])
        self.added = []
        self.deleted = []
        self.flushed = False

    async def execute(self, _stmt):
        return self._exec.pop(0)

    async def get(self, _model, _pk):
        return self._get.pop(0) if self._get else None

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushed = True


class _Org:
    def __init__(self, is_public=False):
        self.id = uuid.uuid4()
        self.name = "TAMU"
        self.slug = "tamu"
        self.is_public = is_public
        self.enabled = True


@pytest.mark.asyncio
async def test_create_organization_ok():
    db = _FakeDB(execute_results=[_Result(scalar=None)])  # slug not taken
    org = await svc.create_organization(db, name="TAMU", slug="tamu")
    assert org in db.added
    assert org.slug == "tamu"
    assert org.is_public is False
    assert db.flushed


@pytest.mark.asyncio
async def test_create_organization_duplicate_slug_conflicts():
    db = _FakeDB(execute_results=[_Result(scalar=uuid.uuid4())])  # slug taken
    with pytest.raises(svc.OrgConflict):
        await svc.create_organization(db, name="TAMU", slug="tamu")


@pytest.mark.asyncio
async def test_update_organization_not_found():
    db = _FakeDB(get_results=[None])
    with pytest.raises(svc.OrgNotFound):
        await svc.update_organization(db, uuid.uuid4(), name="New")


@pytest.mark.asyncio
async def test_update_organization_sets_fields():
    org = _Org()
    db = _FakeDB(get_results=[org])
    _org, enabled_changed = await svc.update_organization(
        db, org.id, name="Texas A&M", enabled=False
    )
    assert org.name == "Texas A&M"
    assert org.enabled is False
    assert enabled_changed is True  # was True, now False


@pytest.mark.asyncio
async def test_update_organization_reports_no_enabled_change_on_echo():
    # Regression (V10): re-sending the current `enabled` value (or omitting it) is
    # NOT a toggle — the route must not log a public-sign-in toggle for a no-op.
    org = _Org()  # enabled True
    db = _FakeDB(get_results=[org])
    _org, enabled_changed = await svc.update_organization(
        db, org.id, name="New Name", enabled=True
    )
    assert enabled_changed is False
    db = _FakeDB(get_results=[org])
    _org, enabled_changed = await svc.update_organization(db, org.id, name="Only Name")
    assert enabled_changed is False


@pytest.mark.asyncio
async def test_delete_public_org_is_protected():
    pub = _Org(is_public=True)
    db = _FakeDB(get_results=[pub])
    with pytest.raises(svc.OrgProtected):
        await svc.delete_organization(db, pub.id)
    assert pub not in db.deleted


@pytest.mark.asyncio
async def test_delete_regular_org_ok():
    org = _Org()
    # execute() is the "restricted solely to this org" check -> no such workspaces.
    db = _FakeDB(get_results=[org], execute_results=[_Result(scalars=[])])
    await svc.delete_organization(db, org.id)
    assert org in db.deleted


@pytest.mark.asyncio
async def test_delete_org_blocked_when_referenced_by_allow_list():
    org = _Org()
    ref_ws = uuid.uuid4()
    # The org is referenced by a workspace's allow-list -> delete must be refused
    # (cascading it away could empty the list and flip that workspace open to all).
    db = _FakeDB(get_results=[org], execute_results=[_Result(scalars=[ref_ws])])
    with pytest.raises(svc.OrgProtected):
        await svc.delete_organization(db, org.id)
    assert org not in db.deleted


@pytest.mark.asyncio
async def test_add_domain_normalizes_and_inserts():
    org = _Org()
    db = _FakeDB(get_results=[org], execute_results=[_Result(scalar=None)])
    d = await svc.add_domain(db, org.id, "  Mail.TAMU.edu ", include_subdomains=True)
    assert d.domain == "mail.tamu.edu"  # normalized
    assert d.include_subdomains is True
    assert d in db.added


@pytest.mark.asyncio
async def test_add_domain_to_public_org_is_protected():
    pub = _Org(is_public=True)
    db = _FakeDB(get_results=[pub])
    with pytest.raises(svc.OrgProtected):
        await svc.add_domain(db, pub.id, "tamu.edu", include_subdomains=False)


@pytest.mark.asyncio
async def test_add_invalid_domain_rejected():
    org = _Org()
    db = _FakeDB(get_results=[org])
    with pytest.raises(ValueError):
        await svc.add_domain(db, org.id, "not-a-domain", include_subdomains=False)


@pytest.mark.asyncio
async def test_add_duplicate_domain_conflicts():
    org = _Org()
    db = _FakeDB(get_results=[org], execute_results=[_Result(scalar=uuid.uuid4())])
    with pytest.raises(svc.OrgConflict):
        await svc.add_domain(db, org.id, "tamu.edu", include_subdomains=False)


@pytest.mark.asyncio
async def test_remove_domain_wrong_org_not_found():
    class _Dom:
        id = uuid.uuid4()
        organization_id = uuid.uuid4()  # belongs to a different org

    db = _FakeDB(get_results=[_Dom()])
    with pytest.raises(svc.OrgNotFound):
        # the domain exists but its organization_id != the org_id we pass, so
        # the cross-org guard (not the missing-row branch) fires.
        await svc.remove_domain(db, uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_set_allowed_orgs_replaces_and_validates():
    ws_id = uuid.uuid4()
    org_a, org_b = _Org(), _Org()
    a, b = org_a.id, org_b.id
    # get(Workspace) -> exists; execute #1 validates ids (both found, enabled,
    # non-public); execute #2 is the delete of existing rows.
    db = _FakeDB(
        get_results=[object()],
        execute_results=[_Result(scalars=[org_a, org_b]), _Result()],
    )
    await svc.set_workspace_allowed_orgs(db, ws_id, [a, b, a])  # dup ignored
    added = [o for o in db.added if isinstance(o, WorkspaceAllowedOrganization)]
    assert {o.organization_id for o in added} == {a, b}
    assert all(o.workspace_id == ws_id for o in added)


@pytest.mark.asyncio
async def test_set_allowed_orgs_unknown_id_rejected():
    ws_id = uuid.uuid4()
    org_a = _Org()
    a, missing = org_a.id, uuid.uuid4()
    db = _FakeDB(get_results=[object()], execute_results=[_Result(scalars=[org_a])])
    with pytest.raises(ValueError, match="Unknown organization ids"):
        await svc.set_workspace_allowed_orgs(db, ws_id, [a, missing])


@pytest.mark.asyncio
async def test_set_allowed_orgs_rejects_public_org():
    # Regression (V5): allow-listing the public catch-all would lock out every real
    # member (their org != public) — reject it.
    ws_id = uuid.uuid4()
    pub = _Org(is_public=True)
    db = _FakeDB(get_results=[object()], execute_results=[_Result(scalars=[pub])])
    with pytest.raises(ValueError, match="enabled, non-public"):
        await svc.set_workspace_allowed_orgs(db, ws_id, [pub.id])
    assert not [o for o in db.added if isinstance(o, WorkspaceAllowedOrganization)]


@pytest.mark.asyncio
async def test_set_allowed_orgs_rejects_disabled_org():
    # Regression (V5): a disabled org can never mint a token, so allow-listing only
    # it would brick the workspace — reject it.
    ws_id = uuid.uuid4()
    org = _Org()
    org.enabled = False
    db = _FakeDB(get_results=[object()], execute_results=[_Result(scalars=[org])])
    with pytest.raises(ValueError, match="enabled, non-public"):
        await svc.set_workspace_allowed_orgs(db, ws_id, [org.id])
    assert not [o for o in db.added if isinstance(o, WorkspaceAllowedOrganization)]


@pytest.mark.asyncio
async def test_set_allowed_orgs_workspace_not_found():
    db = _FakeDB(get_results=[None])
    with pytest.raises(svc.OrgNotFound):
        await svc.set_workspace_allowed_orgs(db, uuid.uuid4(), [])


@pytest.mark.asyncio
async def test_set_allowed_orgs_empty_clears():
    ws_id = uuid.uuid4()
    db = _FakeDB(get_results=[object()], execute_results=[_Result()])  # delete only
    await svc.set_workspace_allowed_orgs(db, ws_id, [])
    assert not [o for o in db.added if isinstance(o, WorkspaceAllowedOrganization)]


@pytest.mark.asyncio
async def test_list_org_users_returns_users_and_total():
    user = object()
    # execute #1 = count, execute #2 = page rows of (user, workspace_count).
    db = _FakeDB(
        get_results=[object()],
        execute_results=[_Result(scalar=3), _Result(rows=[(user, 2)])],
    )
    users, total = await svc.list_org_users(db, uuid.uuid4(), page=1, page_size=10)
    assert total == 3
    assert users == [(user, 2)]


@pytest.mark.asyncio
async def test_list_org_users_empty_org():
    db = _FakeDB(
        get_results=[object()], execute_results=[_Result(scalar=0), _Result(rows=[])]
    )
    users, total = await svc.list_org_users(db, uuid.uuid4())
    assert users == [] and total == 0


@pytest.mark.asyncio
async def test_list_org_users_unknown_org_raises():
    db = _FakeDB(get_results=[None])
    with pytest.raises(svc.OrgNotFound):
        await svc.list_org_users(db, uuid.uuid4())
