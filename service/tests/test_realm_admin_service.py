# service/tests/test_realm_admin_service.py
"""Admin-facing realm_service ops: update, delete (cache-invalidating), list members,
and the has-grants check that powers the join-with-existing-grants warning."""

import uuid

import pytest

from src.models.realm import Realm
from src.models.service_app import ServiceApp


def _app(realm_id=None) -> ServiceApp:
    return ServiceApp(
        id=uuid.uuid4(),
        name="Docs",
        service_name="docs",
        key_hash="x" * 64,
        key_prefix="sk_xxxx****",
        allowed_origins=[],
        allowed_idp_audiences=[],
        realm_id=realm_id,
    )


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _Result:
    def __init__(self, items=None, first=None):
        self._items = items or []
        self._first = first

    def scalars(self):
        return _Scalars(self._items)

    def first(self):
        return self._first


class _FakeDB:
    """get() -> preset obj; execute() -> queued results (FIFO); records delete()."""

    def __init__(self, get_result=None, results=None):
        self._get = get_result
        self._results = list(results or [])
        self.deleted = []

    async def get(self, _model, _pk):
        return self._get

    async def execute(self, _stmt):
        return self._results.pop(0)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        pass


def test_realm_slug_pattern_is_letter_start():
    import re

    from src.schemas.validators import REALM_SLUG_PATTERN

    assert re.match(REALM_SLUG_PATTERN, "acme-suite")
    assert re.match(REALM_SLUG_PATTERN, "acme")  # no-hyphen single word is valid
    assert not re.match(REALM_SLUG_PATTERN, "9acme")  # must start with a letter
    assert not re.match(REALM_SLUG_PATTERN, "-acme")
    assert not re.match(REALM_SLUG_PATTERN, "acme-")


@pytest.mark.asyncio
async def test_update_realm_sets_provided_fields():
    from src.services import realm_service

    realm = Realm(id=uuid.uuid4(), name="Old", slug="acme-suite", m2m_ttl_s=300)
    out = await realm_service.update_realm(
        _FakeDB(get_result=realm), realm.id, name="New", m2m_ttl_s=120, is_active=False
    )
    assert out.name == "New"
    assert out.m2m_ttl_s == 120
    assert out.is_active is False


@pytest.mark.asyncio
async def test_update_realm_missing_returns_none():
    from src.services import realm_service

    assert (
        await realm_service.update_realm(_FakeDB(get_result=None), uuid.uuid4()) is None
    )


@pytest.mark.asyncio
async def test_delete_realm_deletes(monkeypatch):
    # Cache invalidation moved to the route, AFTER commit — ordering covered by
    # tests/test_cache_invalidation_ordering.py.
    from src.services import realm_service

    realm = Realm(id=uuid.uuid4(), name="A", slug="acme-suite", m2m_ttl_s=300)
    db = _FakeDB(get_result=realm)
    assert await realm_service.delete_realm(db, realm.id) is True
    assert realm in db.deleted


@pytest.mark.asyncio
async def test_delete_realm_missing_returns_false():
    from src.services import realm_service

    assert (
        await realm_service.delete_realm(_FakeDB(get_result=None), uuid.uuid4())
        is False
    )


@pytest.mark.asyncio
async def test_list_members_returns_apps():
    from src.services import realm_service

    apps = [_app(), _app()]
    out = await realm_service.list_members(
        _FakeDB(results=[_Result(items=apps)]), uuid.uuid4()
    )
    assert out == apps


@pytest.mark.asyncio
async def test_service_app_has_grants_true_when_action_exists():
    from src.services import realm_service

    # First execute() (ServiceAction probe) returns a row -> short-circuits True.
    db = _FakeDB(results=[_Result(first=("id",))])
    assert await realm_service.service_app_has_grants(db, "docs") is True


@pytest.mark.asyncio
async def test_service_app_has_grants_false_when_none():
    from src.services import realm_service

    # ServiceAction probe empty, then ResourcePermission probe empty -> False.
    db = _FakeDB(results=[_Result(first=None), _Result(first=None)])
    assert await realm_service.service_app_has_grants(db, "docs") is False
