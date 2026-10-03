"""get_realm_by_slug resolves a realm by its shared-scope slug; realm schemas."""

import uuid

import pytest

from src.models.realm import Realm


class _Result:
    def __init__(self, realm):
        self._realm = realm

    def scalar_one_or_none(self):
        return self._realm


class _FakeDB:
    def __init__(self, realm):
        self._realm = realm

    async def execute(self, _stmt):
        return _Result(self._realm)


@pytest.mark.asyncio
async def test_get_realm_by_slug_found():
    from src.services import realm_service

    realm = Realm(id=uuid.uuid4(), name="Acme Suite", slug="acme-suite", m2m_ttl_s=300)
    out = await realm_service.get_realm_by_slug(_FakeDB(realm), "acme-suite")
    assert out is realm


@pytest.mark.asyncio
async def test_get_realm_by_slug_missing_returns_none():
    from src.services import realm_service

    out = await realm_service.get_realm_by_slug(_FakeDB(None), "nope")
    assert out is None


def test_whoami_response_standalone_has_null_realm():
    from src.schemas.realm import WhoamiResponse

    r = WhoamiResponse(service_name="docs", effective_scope="docs", realm=None)
    assert r.realm is None
    assert r.effective_scope == "docs"


def test_whoami_response_member_carries_realm_info():
    from src.schemas.realm import RealmInfo, WhoamiResponse

    r = WhoamiResponse(
        service_name="docs",
        effective_scope="acme-suite",
        realm=RealmInfo(slug="acme-suite", name="Acme Suite"),
    )
    assert r.realm.slug == "acme-suite"
    assert r.effective_scope == "acme-suite"
