"""Behavioral tests for /admin/realms CRUD — require_admin + get_db overridden,
realm_service + activity log mocked (house behavioral style)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import admin_routes
from src.api.admin_routes import router as admin_router
from src.api.dependencies import require_admin
from src.database import get_db
from src.models.realm import Realm


class _FakeDB:
    async def commit(self):
        pass


def _build_app(monkeypatch):
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_admin] = lambda: {"sub": str(uuid.uuid4())}

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db

    async def _log(*a, **k):
        return None

    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)

    async def _noop_invalidate():
        return None

    monkeypatch.setattr(
        admin_routes.service_app_service, "invalidate_cache", _noop_invalidate
    )
    return app


def _realm(slug="acme-suite", name="Acme Suite"):
    return Realm(
        id=uuid.uuid4(),
        slug=slug,
        name=name,
        m2m_ttl_s=300,
        is_active=True,
        created_at=datetime.now(UTC),
    )


def test_create_realm_returns_201_and_audits(monkeypatch):
    app = _build_app(monkeypatch)
    created = {}

    async def _create(_db, *, name, slug, m2m_ttl_s=300, created_by=None):
        created["name"], created["slug"] = name, slug
        return _realm(slug=slug, name=name)

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.realm_service, "create_realm", _create)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)

    resp = TestClient(app).post(
        "/admin/realms",
        json={"name": "Acme Suite", "slug": "acme-suite"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 201
    assert resp.json()["slug"] == "acme-suite"
    assert created == {"name": "Acme Suite", "slug": "acme-suite"}
    assert logged["action"] == "realm_created"


def test_list_realms(monkeypatch):
    app = _build_app(monkeypatch)

    async def _list(_db):
        return [_realm()]

    monkeypatch.setattr(admin_routes.realm_service, "list_realms", _list)
    resp = TestClient(app).get("/admin/realms")
    assert resp.status_code == 200
    assert resp.json()[0]["slug"] == "acme-suite"


def test_get_realm_detail_404_when_missing(monkeypatch):
    app = _build_app(monkeypatch)

    async def _get(_db, _id):
        return None

    monkeypatch.setattr(admin_routes.realm_service, "get_realm", _get)
    resp = TestClient(app).get(f"/admin/realms/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_update_realm_audits(monkeypatch):
    app = _build_app(monkeypatch)

    async def _update(_db, _id, *, name=None, m2m_ttl_s=None, is_active=None):
        return _realm(name=name or "Acme Suite")

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.realm_service, "update_realm", _update)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)
    resp = TestClient(app).patch(
        f"/admin/realms/{uuid.uuid4()}",
        json={"name": "Renamed"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert logged["action"] == "realm_updated"


def test_delete_realm_204_and_audits(monkeypatch):
    app = _build_app(monkeypatch)

    async def _delete(_db, _id):
        return True

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.realm_service, "delete_realm", _delete)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)
    resp = TestClient(app).delete(
        f"/admin/realms/{uuid.uuid4()}", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert resp.status_code == 204
    assert logged["action"] == "realm_deleted"


def test_delete_realm_404_when_missing(monkeypatch):
    app = _build_app(monkeypatch)

    async def _delete(_db, _id):
        return False

    monkeypatch.setattr(admin_routes.realm_service, "delete_realm", _delete)
    resp = TestClient(app).delete(
        f"/admin/realms/{uuid.uuid4()}", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert resp.status_code == 404
