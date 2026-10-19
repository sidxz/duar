# service/tests/test_cache_invalidation_ordering.py
"""The service-key/origin Redis cache must be cleared AFTER db.commit().

Invalidating pre-commit opens a race: a concurrent request misses the emptied
cache and rebuilds it from the not-yet-committed DB state (READ COMMITTED can't
see the flush), resurrecting the pre-mutation mapping — e.g. a just-rotated-out
(leaked) service key — for a full cache TTL (5 min).

Covers the three highest-stakes admin routes; all mutating service-app/realm
routes share the same commit-then-invalidate shape in admin_routes.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import admin_routes
from src.api.admin_routes import router as admin_router
from src.api.dependencies import require_admin
from src.database import get_db
from src.middleware.rate_limit import limiter
from src.models.realm import Realm
from src.models.service_app import ServiceApp
from src.services import service_app_service

_XRW = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


class _FakeRedis:
    def __init__(self, events: list[str]):
        self._events = events

    async def delete(self, *keys):
        self._events.append("invalidate")


class _FakeDB:
    """Serves db.get/flush/refresh for the real service layer; records commits."""

    def __init__(self, events: list[str], obj):
        self._events = events
        self._obj = obj
        self.deleted = []

    async def get(self, _model, _pk):
        return self._obj

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self._events.append("commit")

    async def refresh(self, _obj):
        pass


def _build_app(monkeypatch, events: list[str], obj) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_admin] = lambda: {"sub": str(uuid.uuid4())}

    async def _db():
        yield _FakeDB(events, obj)

    app.dependency_overrides[get_db] = _db

    async def _log(*a, **k):
        return None

    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)

    async def _redis():
        return _FakeRedis(events)

    monkeypatch.setattr(service_app_service, "get_redis", _redis)
    return app


def _service_app(realm_id=None) -> ServiceApp:
    now = datetime.now(UTC)
    return ServiceApp(
        id=uuid.uuid4(),
        name="Docs",
        service_name="docs",
        key_hash="x" * 64,
        key_prefix="sk_xxxx****",
        is_active=True,
        allowed_origins=[],
        allowed_idp_audiences=[],
        realm_id=realm_id,
        created_at=now,
        updated_at=now,
    )


def _assert_invalidated_after_commit(events: list[str]):
    assert "invalidate" in events, "cache must be invalidated"
    assert "commit" in events, "transaction must be committed"
    assert events.index("commit") < events.index("invalidate"), (
        f"cache invalidated before commit — stale-rebuild race (events: {events})"
    )


def test_rotate_key_invalidates_cache_only_after_commit(monkeypatch):
    events: list[str] = []
    svc_app = _service_app()
    app = _build_app(monkeypatch, events, svc_app)

    resp = TestClient(app).post(
        f"/admin/service-apps/{svc_app.id}/rotate-key", headers=_XRW
    )
    assert resp.status_code == 200
    _assert_invalidated_after_commit(events)


def test_remove_realm_member_invalidates_cache_only_after_commit(monkeypatch):
    events: list[str] = []
    realm_id = uuid.uuid4()
    svc_app = _service_app(realm_id=realm_id)
    app = _build_app(monkeypatch, events, svc_app)

    resp = TestClient(app).delete(
        f"/admin/realms/{realm_id}/members/{svc_app.id}", headers=_XRW
    )
    assert resp.status_code == 204
    _assert_invalidated_after_commit(events)


def test_delete_realm_invalidates_cache_only_after_commit(monkeypatch):
    events: list[str] = []
    realm = Realm(id=uuid.uuid4(), name="Acme Suite", slug="acme-suite", m2m_ttl_s=300)
    app = _build_app(monkeypatch, events, realm)

    resp = TestClient(app).delete(f"/admin/realms/{realm.id}", headers=_XRW)
    assert resp.status_code == 204
    _assert_invalidated_after_commit(events)


def test_create_service_app_refreshes_origins_after_commit(monkeypatch):
    """A service app created with allowed_origins in one shot must enter the
    in-memory CORS origin set immediately — not on the next restart/PATCH.

    Regression: POST /service-apps was the only origin-affecting admin route
    that never called refresh_origins, so a fresh app's origins stayed dark
    until the process restarted or a sibling route rebuilt the set.
    """
    events: list[str] = []
    svc_app = _service_app()
    svc_app.allowed_origins = ["http://localhost:3003"]
    svc_app.created_by = uuid.uuid4()
    app = _build_app(monkeypatch, events, svc_app)

    async def _create(db, **_kwargs):
        return svc_app, "sk_plaintext_key"

    monkeypatch.setattr(admin_routes.service_app_service, "create_service_app", _create)

    async def _refresh(_db):
        events.append("refresh_origins")

    monkeypatch.setattr(admin_routes, "refresh_origins", _refresh)

    resp = TestClient(app).post(
        "/admin/service-apps",
        json={
            "name": "Docs",
            "service_name": "docs",
            "allowed_origins": ["http://localhost:3003"],
        },
        headers=_XRW,
    )
    assert resp.status_code == 201
    assert "refresh_origins" in events, (
        "created origins never enter the CORS set until restart/PATCH"
    )
    assert events.index("commit") < events.index("refresh_origins")
