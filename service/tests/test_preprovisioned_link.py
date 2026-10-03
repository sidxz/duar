"""A pre-provisioned (CSV-imported) user with no SocialAccount must be linkable
on first IdP sign-in, not permanently locked out.

A user with an existing SocialAccount from a *different* provider is still a real
cross-provider collision and must be rejected.
"""

import uuid

import pytest

from src.models.user import SocialAccount, User
from src.services.auth_service import CrossProviderEmailConflict, find_or_create_user


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns queued scalar_one_or_none() values for successive execute() calls."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = False

    async def execute(self, _stmt):
        return _Result(self._results.pop(0))

    async def get(self, _model, _pk):
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


def _bare_user(email):
    u = User(email=email, name="Pre Provisioned")
    u.id = uuid.uuid4()
    u.is_admin = False
    return u


@pytest.mark.asyncio
async def test_preprovisioned_user_is_linked_not_rejected():
    pre = _bare_user("victim@example.com")
    org_id = uuid.uuid4()
    # execute() order: SocialAccount-by-provider (miss), User-by-email (hit),
    # SocialAccount-by-user (none → bare pre-provisioned account).
    session = _FakeSession(results=[None, pre, None])

    user = await find_or_create_user(
        session,
        provider="google",
        provider_user_id="google|1",
        email="victim@example.com",
        name="Victim",
        organization_id=org_id,
    )

    assert user is pre
    assert user.organization_id == org_id
    assert any(isinstance(a, SocialAccount) for a in session.added)
    assert session.committed


@pytest.mark.asyncio
async def test_real_cross_provider_collision_still_rejected():
    other = _bare_user("victim@example.com")
    # Third execute() returns a truthy id → user already has a SocialAccount
    # from a different provider: genuine collision, must reject.
    session = _FakeSession(results=[None, other, uuid.uuid4()])

    with pytest.raises(CrossProviderEmailConflict):
        await find_or_create_user(
            session,
            provider="google",
            provider_user_id="google|1",
            email="victim@example.com",
            name="Victim",
            organization_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_existing_user_org_not_clobbered_by_none():
    """A returning user (social account already exists) keeps their org when the
    caller passes organization_id=None (e.g. admin sign-in that matched no org)."""
    kept = uuid.uuid4()
    existing = _bare_user("admin@corp.com")
    existing.organization_id = kept

    class _Social:
        user_id = existing.id
        provider_data = None

    class _SocialSession:
        def __init__(self):
            self.committed = False

        async def execute(self, _stmt):
            return _Result(_Social())

        async def get(self, _model, _pk):
            return existing

        def add(self, _obj):
            pass

        async def flush(self):
            pass

        async def commit(self):
            self.committed = True

    session = _SocialSession()
    user = await find_or_create_user(
        session,
        provider="google",
        provider_user_id="google|1",
        email="admin@corp.com",
        name="Admin",
        organization_id=None,
    )

    assert user.organization_id == kept
    assert session.committed


@pytest.mark.asyncio
async def test_organization_id_may_be_none_for_new_user():
    # Callers that do not org-gate (e.g. admin sign-in) pass organization_id=None.
    # execute() order: SocialAccount-by-provider (miss), User-by-email (miss) →
    # brand-new user created with a null org.
    session = _FakeSession(results=[None, None])

    user = await find_or_create_user(
        session,
        provider="google",
        provider_user_id="google|9",
        email="new@example.com",
        name="New",
        organization_id=None,
    )

    assert user.organization_id is None
    assert session.committed
