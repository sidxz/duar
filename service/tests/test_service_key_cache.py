"""A lookup that misses the service-key or origin cache reaches the database
only when the miss may be the cache's fault: the cache is gone, or it has not
been rechecked for a few seconds. An unknown key or origin is otherwise
answered from Redis.

Runs the real lookups and reloads against an in-memory stand-in for Redis and a
database that counts its queries.
"""

import hashlib
import uuid
from types import SimpleNamespace

import pytest

from src.services import service_app_service as svc


class _Redis:
    """Just enough of redis.asyncio for the two caches."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, object] = {}
        self.ttls: dict[str, int | None] = {}

    async def hmget(self, key, fields):
        return [self.hashes.get(key, {}).get(f) for f in fields]

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        self.ttls[key] = ex
        return True

    async def delete(self, *keys):
        for key in keys:
            self.hashes.pop(key, None)
            self.strings.pop(key, None)

    def pipeline(self):
        return _Pipeline(self)


class _Pipeline:
    def __init__(self, redis):
        self._redis, self._ops = redis, []

    def delete(self, key):
        self._ops.append(lambda: self._redis.hashes.pop(key, None))

    def hset(self, key, field, value):
        self._ops.append(
            lambda: self._redis.hashes.setdefault(key, {}).__setitem__(field, value)
        )

    def expire(self, key, ttl):
        self._ops.append(lambda: self._redis.ttls.__setitem__(key, ttl))

    async def execute(self):
        for op in self._ops:
            op()


class _DB:
    """The service_apps table as a list; counts the reloads that read it."""

    def __init__(self):
        self.apps = []
        self.reloads = 0

    def add(self, key, origin=None):
        app = SimpleNamespace(
            id=uuid.uuid4(),
            service_name=f"app{len(self.apps)}",
            key_hash=hashlib.sha256(key.encode()).hexdigest(),
            allowed_origins=[origin] if origin else [],
            is_active=True,
            last_used_at=None,
        )
        self.apps.append(app)
        return app

    async def execute(self, _statement):
        self.reloads += 1
        apps = self.apps
        return SimpleNamespace(
            all=lambda: [(app, None) for app in apps],
            scalars=lambda: SimpleNamespace(all=lambda: apps),
        )

    async def get(self, _model, app_id):
        return next((app for app in self.apps if app.id == app_id), None)

    async def commit(self):
        pass


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(svc, "get_redis", _get_redis)
    return fake


def _recheck_window_passes(redis):
    redis.strings.clear()


@pytest.mark.asyncio
async def test_unknown_keys_do_not_reach_the_database_each_time(redis):
    db = _DB()
    db.add("sk_real")
    for n in range(50):
        assert await svc.validate_key(f"sk_guess_{n}", db) is None
    # Once because the cache was not there, once for the first recheck.
    assert db.reloads == 2
    _recheck_window_passes(redis)
    assert await svc.validate_key("sk_another_guess", db) is None
    assert db.reloads == 3


@pytest.mark.asyncio
async def test_a_known_key_is_found_and_found_again_after_the_cache_is_dropped(redis):
    db = _DB()
    app = db.add("sk_real")
    assert await svc.validate_key("sk_real", db) == (app.service_name, app.id, None)
    assert await svc.validate_key("sk_real", db) == (app.service_name, app.id, None)
    assert db.reloads == 1
    # What a route does after committing a write: the next lookup reloads at
    # once, however recently unknown keys were tried.
    assert await svc.validate_key("sk_guess", db) is None
    await svc.invalidate_cache()
    assert await svc.validate_key("sk_real", db) == (app.service_name, app.id, None)


@pytest.mark.asyncio
async def test_a_key_the_cache_missed_is_picked_up_at_the_next_recheck(redis):
    db = _DB()
    db.add("sk_first")
    assert await svc.validate_key("sk_first", db) is not None
    assert await svc.validate_key("sk_guess", db) is None  # uses up the recheck
    # A write whose reload raced it: in the database, not in the cache.
    late = db.add("sk_late")
    assert await svc.validate_key("sk_late", db) is None
    _recheck_window_passes(redis)
    assert await svc.validate_key("sk_late", db) == (late.service_name, late.id, None)


@pytest.mark.asyncio
async def test_a_deactivated_apps_key_is_refused_while_the_cache_still_holds_it(redis):
    db = _DB()
    app = db.add("sk_real")
    assert await svc.validate_key("sk_real", db) is not None
    app.is_active = False
    assert await svc.validate_key("sk_real", db) is None


@pytest.mark.asyncio
async def test_everything_the_caches_keep_in_redis_expires(redis):
    db = _DB()
    for n in range(2):  # the first builds the cache, the second sets its marker
        await svc.validate_key(f"sk_guess_{n}", db)
        await svc.validate_origin(f"https://guess{n}.example", db)
    assert redis.ttls == {
        svc._CACHE_KEY: svc._CACHE_TTL,
        svc._ORIGIN_CACHE_KEY: svc._CACHE_TTL,
        f"{svc._CACHE_KEY}:recheck": svc._RECHECK_SECONDS,
        f"{svc._ORIGIN_CACHE_KEY}:recheck": svc._RECHECK_SECONDS,
    }


@pytest.mark.asyncio
async def test_a_deployment_with_no_apps_still_has_a_cache(redis):
    db = _DB()
    for n in range(20):
        assert await svc.validate_key(f"sk_guess_{n}", db) is None
    assert db.reloads == 2


@pytest.mark.asyncio
async def test_origins_follow_the_same_rule(redis):
    db = _DB()
    app = db.add("sk_real", origin="https://notes.example")
    assert await svc.validate_origin("https://notes.example", db) == (
        app.service_name,
        app.id,
    )
    for n in range(50):
        assert await svc.validate_origin(f"https://guess{n}.example", db) is None
    assert db.reloads == 2
    # The marker field is not an origin, whatever a client sends.
    assert await svc.validate_origin(svc._BUILT, db) is None
