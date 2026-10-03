"""Tests for PermissionClient using respx to mock httpx."""

import uuid

import httpx
import pytest
import respx

from duar_auth.permissions import PermissionCheck, PermissionClient, _TTLCache


@pytest.fixture()
def client():
    return PermissionClient("https://auth.test", "docu-store", service_key="sk-test")


@pytest.fixture()
def cached_client():
    return PermissionClient("https://auth.test", "docu-store", service_key="sk-test", cache_ttl=60)


RES_ID = uuid.uuid4()
WS_ID = uuid.uuid4()
OWNER_ID = uuid.uuid4()


class TestPermissionClient:
    @respx.mock
    async def test_can_allowed(self, client):
        respx.post("https://auth.test/permissions/check").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "service_name": "docu-store",
                            "resource_type": "document",
                            "resource_id": str(RES_ID),
                            "action": "view",
                            "allowed": True,
                        }
                    ]
                },
            )
        )
        result = await client.can("tok", "document", RES_ID, "view")
        assert result is True

    @respx.mock
    async def test_can_denied(self, client):
        respx.post("https://auth.test/permissions/check").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "service_name": "docu-store",
                            "resource_type": "document",
                            "resource_id": str(RES_ID),
                            "action": "edit",
                            "allowed": False,
                        }
                    ]
                },
            )
        )
        result = await client.can("tok", "document", RES_ID, "edit")
        assert result is False

    @respx.mock
    async def test_check_batch(self, client):
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        respx.post("https://auth.test/permissions/check").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "service_name": "docu-store",
                            "resource_type": "doc",
                            "resource_id": str(r1),
                            "action": "view",
                            "allowed": True,
                        },
                        {
                            "service_name": "docu-store",
                            "resource_type": "doc",
                            "resource_id": str(r2),
                            "action": "edit",
                            "allowed": False,
                        },
                    ]
                },
            )
        )
        checks = [
            PermissionCheck("docu-store", "doc", r1, "view"),
            PermissionCheck("docu-store", "doc", r2, "edit"),
        ]
        results = await client.check("tok", checks)
        assert len(results) == 2
        assert results[0].allowed is True
        assert results[1].allowed is False

    @respx.mock
    async def test_register_resource(self, client):
        respx.post("https://auth.test/permissions/register").mock(return_value=httpx.Response(200, json={"id": "abc"}))
        result = await client.register_resource("document", RES_ID, WS_ID, OWNER_ID)
        assert result == {"id": "abc"}

    @respx.mock
    async def test_accessible(self, client):
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        respx.post("https://auth.test/permissions/accessible").mock(
            return_value=httpx.Response(
                200,
                json={"resource_ids": [str(r1), str(r2)], "has_full_access": False},
            )
        )
        ids, full = await client.accessible("tok", "document", "view", WS_ID)
        assert len(ids) == 2
        assert full is False

    @respx.mock
    async def test_accessible_full_access(self, client):
        respx.post("https://auth.test/permissions/accessible").mock(
            return_value=httpx.Response(
                200,
                json={"resource_ids": [], "has_full_access": True},
            )
        )
        ids, full = await client.accessible("tok", "document", "view", WS_ID)
        assert ids == []
        assert full is True

    async def test_context_manager(self):
        async with PermissionClient("https://auth.test", "svc") as client:
            assert client.service_name == "svc"

    def test_headers_with_service_key_and_token(self, client):
        h = client._headers("my-jwt")
        assert h["X-Service-Key"] == "sk-test"
        assert h["Authorization"] == "Bearer my-jwt"

    def test_headers_without_token(self, client):
        h = client._headers()
        assert "Authorization" not in h
        assert h["X-Service-Key"] == "sk-test"


class TestTTLCache:
    def test_set_and_get(self):
        cache = _TTLCache(ttl=60)
        cache.set(("a", "b"), 42)
        assert cache.get(("a", "b")) == 42

    def test_miss_returns_none(self):
        cache = _TTLCache(ttl=60)
        assert cache.get(("nope",)) is None

    def test_expiry(self, monkeypatch):
        import time as _time

        t = [100.0]
        monkeypatch.setattr(_time, "monotonic", lambda: t[0])

        # Patch the module-level import in permissions.py
        from duar_auth import permissions

        monkeypatch.setattr(permissions.time, "monotonic", lambda: t[0])

        cache = _TTLCache(ttl=10)
        cache.set(("k",), "val")
        assert cache.get(("k",)) == "val"

        t[0] = 111.0  # 11 seconds later → expired
        assert cache.get(("k",)) is None

    def test_invalidate_by_prefix(self):
        cache = _TTLCache(ttl=60)
        cache.set(("can", "tok1", "doc", "123", "view"), True)
        cache.set(("accessible", "tok1", "doc", "view", "ws1", None), ([], True))
        cache.set(("other", "x"), "keep")
        cache.invalidate("can", "accessible")
        assert cache.get(("can", "tok1", "doc", "123", "view")) is None
        assert cache.get(("accessible", "tok1", "doc", "view", "ws1", None)) is None
        assert cache.get(("other", "x")) == "keep"

    def test_clear(self):
        cache = _TTLCache(ttl=60)
        cache.set(("a",), 1)
        cache.set(("b",), 2)
        cache.clear()
        assert len(cache) == 0

    def test_maxsize_bounds_memory_when_nothing_expired(self):
        # High-cardinality burst within one TTL window: lazy expiry evicts
        # nothing, so maxsize must be enforced by dropping oldest entries.
        cache = _TTLCache(ttl=60, maxsize=10)
        for i in range(100):
            cache.set(("k", i), i)
        assert len(cache) <= 15  # never above the maxsize * 1.5 trigger
        assert cache.get(("k", 99)) == 99  # newest entries survive
        assert cache.get(("k", 0)) is None  # oldest evicted first


class TestPermissionClientCache:
    @respx.mock
    async def test_accessible_cached_on_second_call(self, cached_client):
        r1 = uuid.uuid4()
        route = respx.post("https://auth.test/permissions/accessible").mock(
            return_value=httpx.Response(
                200,
                json={"resource_ids": [str(r1)], "has_full_access": False},
            )
        )
        # First call → hits Duar
        ids1, full1 = await cached_client.accessible("tok", "document", "view", WS_ID)
        assert len(ids1) == 1
        assert route.call_count == 1

        # Second call → served from cache, no HTTP
        ids2, full2 = await cached_client.accessible("tok", "document", "view", WS_ID)
        assert ids2 == ids1
        assert route.call_count == 1  # still 1

    @respx.mock
    async def test_can_cached_on_second_call(self, cached_client):
        route = respx.post("https://auth.test/permissions/check").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "service_name": "docu-store",
                            "resource_type": "document",
                            "resource_id": str(RES_ID),
                            "action": "view",
                            "allowed": True,
                        }
                    ]
                },
            )
        )
        assert await cached_client.can("tok", "document", RES_ID, "view") is True
        assert route.call_count == 1
        assert await cached_client.can("tok", "document", RES_ID, "view") is True
        assert route.call_count == 1  # cached

    @respx.mock
    async def test_share_invalidates_cache(self, cached_client):
        """After a share(), cached accessible/can results should be cleared."""
        r1 = uuid.uuid4()
        accessible_route = respx.post("https://auth.test/permissions/accessible").mock(
            return_value=httpx.Response(
                200,
                json={"resource_ids": [str(r1)], "has_full_access": False},
            )
        )
        # Prime the cache
        await cached_client.accessible("tok", "document", "view", WS_ID)
        assert accessible_route.call_count == 1

        # share() triggers invalidation
        perm_id = uuid.uuid4()
        respx.get(f"https://auth.test/permissions/resource/docu-store/document/{r1}").mock(
            return_value=httpx.Response(200, json={"id": str(perm_id)})
        )
        respx.post(f"https://auth.test/permissions/{perm_id}/share").mock(
            return_value=httpx.Response(201, json={"status": "ok"})
        )
        await cached_client.share("tok", "document", r1, "user", uuid.uuid4())

        # Cache should be cleared — next accessible() must hit Duar again
        await cached_client.accessible("tok", "document", "view", WS_ID)
        assert accessible_route.call_count == 2  # original + post-invalidation

    @respx.mock
    async def test_no_cache_when_ttl_zero(self, client):
        """Default client (cache_ttl=0) should not cache."""
        route = respx.post("https://auth.test/permissions/accessible").mock(
            return_value=httpx.Response(
                200,
                json={"resource_ids": [], "has_full_access": True},
            )
        )
        await client.accessible("tok", "document", "view", WS_ID)
        await client.accessible("tok", "document", "view", WS_ID)
        assert route.call_count == 2  # no caching

    @respx.mock
    async def test_different_tokens_not_shared(self, cached_client):
        """Cache entries are keyed by token hash — different users don't share."""
        r1 = uuid.uuid4()
        route = respx.post("https://auth.test/permissions/accessible").mock(
            return_value=httpx.Response(
                200,
                json={"resource_ids": [str(r1)], "has_full_access": False},
            )
        )
        await cached_client.accessible("user-a-token", "document", "view", WS_ID)
        await cached_client.accessible("user-b-token", "document", "view", WS_ID)
        assert route.call_count == 2  # different tokens → different cache keys
