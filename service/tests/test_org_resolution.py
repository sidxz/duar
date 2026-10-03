"""resolve_organization + workspace_allows_org, exercised with fake sessions."""

import types
import uuid

import pytest

from src.services import organization_service as org_svc


class _ExecResult:
    def __init__(self, *, rows=None, scalar=None, scalars=None):
        self._rows = rows or []
        self._scalar = scalar
        self._scalars = scalars or []

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        parent = self

        class _S:
            def all(self_inner):
                return parent._scalars

        return _S()


class _FakeSession:
    """Serves queued _ExecResult objects for successive execute() calls and a
    dict for get()."""

    def __init__(self, exec_results, get_map=None):
        self._exec = list(exec_results)
        self._get = get_map or {}

    async def execute(self, _stmt):
        return self._exec.pop(0)

    async def get(self, _model, pk):
        return self._get.get(pk)


class _Org:
    def __init__(self, *, enabled=True, is_public=False):
        self.id = uuid.uuid4()
        self.slug = "tamu"
        self.enabled = enabled
        self.is_public = is_public


@pytest.mark.asyncio
async def test_resolve_matched_org_returned():
    org = _Org()
    session = _FakeSession(
        exec_results=[_ExecResult(rows=[(org.id, "tamu.edu", False, True)])],
        get_map={org.id: org},
    )
    result = await org_svc.resolve_organization(session, "alice@tamu.edu")
    assert result is org


@pytest.mark.asyncio
async def test_resolve_disabled_org_blocks_not_fallthrough():
    # A claimed domain whose ONLY claimant is a DISABLED org must return None
    # (kill-switch), NOT fall through to the public org — even when public sign-in
    # is enabled.
    org = _Org(enabled=False)
    session = _FakeSession(
        exec_results=[_ExecResult(rows=[(org.id, "tamu.edu", False, False)])],
        get_map={org.id: org},
    )
    result = await org_svc.resolve_organization(session, "alice@tamu.edu")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_disabled_org_does_not_shadow_enabled_org():
    # Regression (V4): a disabled org's more-specific rule must NOT shadow an
    # enabled org that also claims the (sub)domain. enabled 'tamu.edu'+subdomains
    # must win over disabled exact 'sub.tamu.edu' for a user at sub.tamu.edu.
    disabled = _Org(enabled=False)
    enabled = _Org(enabled=True)
    session = _FakeSession(
        exec_results=[
            _ExecResult(
                rows=[
                    (disabled.id, "sub.tamu.edu", False, False),
                    (enabled.id, "tamu.edu", True, True),
                ]
            )
        ],
        get_map={disabled.id: disabled, enabled.id: enabled},
    )
    result = await org_svc.resolve_organization(session, "x@sub.tamu.edu")
    assert result is enabled


@pytest.mark.asyncio
async def test_resolve_falls_back_to_public():
    public = object()
    session = _FakeSession(
        exec_results=[
            _ExecResult(rows=[]),  # no domain match
            _ExecResult(scalar=public),  # enabled public org
        ]
    )
    result = await org_svc.resolve_organization(session, "someone@gmail.com")
    assert result is public


@pytest.mark.asyncio
async def test_resolve_none_when_no_match_and_no_public():
    session = _FakeSession(
        exec_results=[_ExecResult(rows=[]), _ExecResult(scalar=None)]
    )
    result = await org_svc.resolve_organization(session, "someone@gmail.com")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_malformed_email_short_circuits_to_none():
    # No execute() results queued: a malformed domain must not hit the DB.
    session = _FakeSession(exec_results=[])
    result = await org_svc.resolve_organization(session, "not-an-email")
    assert result is None


@pytest.mark.asyncio
async def test_workspace_open_when_no_rows():
    session = _FakeSession(exec_results=[_ExecResult(scalars=[])])
    assert (
        await org_svc.workspace_allows_org(session, uuid.uuid4(), uuid.uuid4()) is True
    )


@pytest.mark.asyncio
async def test_workspace_restricts_to_allowed_set():
    allowed = uuid.uuid4()
    other = uuid.uuid4()
    session = _FakeSession(exec_results=[_ExecResult(scalars=[allowed])])
    assert await org_svc.workspace_allows_org(session, uuid.uuid4(), allowed) is True

    session = _FakeSession(exec_results=[_ExecResult(scalars=[allowed])])
    assert await org_svc.workspace_allows_org(session, uuid.uuid4(), other) is False


@pytest.mark.asyncio
async def test_workspace_denies_orgless_user_when_restricted():
    allowed = uuid.uuid4()
    session = _FakeSession(exec_results=[_ExecResult(scalars=[allowed])])
    assert await org_svc.workspace_allows_org(session, uuid.uuid4(), None) is False


# ── org_for_claims (the real-org kill-switch at token issuance / refresh) ──


@pytest.mark.asyncio
async def test_org_for_claims_disabled_real_org_raises():
    org = _Org(enabled=False, is_public=False)
    session = _FakeSession(exec_results=[], get_map={org.id: org})
    with pytest.raises(org_svc.OrgDisabled):
        await org_svc.org_for_claims(session, org.id)


@pytest.mark.asyncio
async def test_org_for_claims_disabled_public_is_allowed():
    # Public 'enabled' is a sign-in switch, not a kill-switch for live sessions.
    org = _Org(enabled=False, is_public=True)
    session = _FakeSession(exec_results=[], get_map={org.id: org})
    assert await org_svc.org_for_claims(session, org.id) is org


@pytest.mark.asyncio
async def test_org_for_claims_none_id_returns_none():
    session = _FakeSession(exec_results=[])
    assert await org_svc.org_for_claims(session, None) is None


def test_org_disabled_is_value_error():
    # Token routes map ValueError -> 403; OrgDisabled must be caught by that.
    assert issubclass(org_svc.OrgDisabled, ValueError)


def test_org_claims_shape():
    org = _Org()
    assert org_svc.org_claims(org) == {
        "org_id": str(org.id),
        "org_slug": org.slug,
        "org_is_public": org.is_public,
    }
    assert org_svc.org_claims(None) == {
        "org_id": None,
        "org_slug": None,
        "org_is_public": False,
    }


# ── assert_user_allowed_in_workspace (effective-org membership gate) ──


@pytest.mark.asyncio
async def test_assert_allowed_resolves_orgless_user_from_email():
    # A pre-provisioned NULL-org user whose email domain belongs to an allowed
    # org must NOT be rejected — the gate resolves the effective org.
    org = _Org()  # enabled real org owning tamu.edu
    user = types.SimpleNamespace(organization_id=None, email="alice@tamu.edu")
    session = _FakeSession(
        exec_results=[
            _ExecResult(rows=[(org.id, "tamu.edu", False, True)]),  # resolve domain
            _ExecResult(scalars=[org.id]),  # workspace_allows_org -> allowed
        ],
        get_map={org.id: org},
    )
    await org_svc.assert_user_allowed_in_workspace(session, user, uuid.uuid4())


@pytest.mark.asyncio
async def test_assert_rejects_user_in_disabled_real_org():
    # Regression (V2/V4): a user whose STORED org is a disabled real org must be
    # rejected at membership creation (kill-switch), mirroring token issuance —
    # even when the workspace is open. No workspace_allows_org query is reached.
    org = _Org(enabled=False)
    user = types.SimpleNamespace(organization_id=org.id, email="x@tamu.edu")
    session = _FakeSession(exec_results=[], get_map={org.id: org})
    with pytest.raises(ValueError, match="organization is disabled"):
        await org_svc.assert_user_allowed_in_workspace(session, user, uuid.uuid4())


@pytest.mark.asyncio
async def test_assert_denies_orgless_user_when_domain_unclaimed_and_restricted():
    allowed = uuid.uuid4()
    public = _Org(is_public=True)
    user = types.SimpleNamespace(organization_id=None, email="x@gmail.com")
    session = _FakeSession(
        exec_results=[
            _ExecResult(rows=[]),  # no domain match
            _ExecResult(scalar=public),  # falls back to public org
            _ExecResult(scalars=[allowed]),  # workspace restricted to 'allowed'
        ]
    )
    with pytest.raises(ValueError, match="organization is not permitted"):
        await org_svc.assert_user_allowed_in_workspace(session, user, uuid.uuid4())


@pytest.mark.asyncio
async def test_workspaces_allowing_org():
    ws = uuid.uuid4()
    session = _FakeSession(exec_results=[_ExecResult(scalars=[ws])])
    result = await org_svc.workspaces_allowing_org(session, uuid.uuid4())
    assert result == [ws]


# ── filter_workspaces_allowing_org (batched picker/discovery filter) ──


@pytest.mark.asyncio
async def test_filter_workspaces_allowing_org_open_and_restricted():
    ws_open, ws_allowed, ws_denied = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    org_id = uuid.uuid4()
    session = _FakeSession(
        exec_results=[
            _ExecResult(scalars=[ws_allowed, ws_denied]),  # restricted workspaces
            _ExecResult(scalars=[ws_allowed]),  # those explicitly allowing org_id
        ]
    )
    result = await org_svc.filter_workspaces_allowing_org(
        session, [ws_open, ws_allowed, ws_denied], org_id
    )
    # Open (unrestricted) and the one that allows the org; the denied one is out.
    assert result == {ws_open, ws_allowed}


@pytest.mark.asyncio
async def test_filter_workspaces_allowing_org_none_org_only_open():
    ws_open, ws_restricted = uuid.uuid4(), uuid.uuid4()
    # org None: no second query; only unrestricted workspaces pass.
    session = _FakeSession(exec_results=[_ExecResult(scalars=[ws_restricted])])
    result = await org_svc.filter_workspaces_allowing_org(
        session, [ws_open, ws_restricted], None
    )
    assert result == {ws_open}


@pytest.mark.asyncio
async def test_filter_workspaces_allowing_org_empty_short_circuits():
    # No workspaces => no query issued at all.
    session = _FakeSession(exec_results=[])
    assert (
        await org_svc.filter_workspaces_allowing_org(session, [], uuid.uuid4()) == set()
    )
