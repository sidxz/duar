# service/tests/test_realm_admin_membership.py
"""Behavioral tests for /admin/realms/{id}/members — list (with has_grants), add
(one-realm-max 409 guard), remove. require_admin + get_db overridden; services mocked."""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import admin_routes
from src.api.admin_routes import router as admin_router
from src.api.dependencies import require_admin
from src.database import get_db
from src.models.realm import Realm
from src.models.service_app import ServiceApp


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


def _realm():
    return Realm(
        id=uuid.uuid4(),
        slug="acme-suite",
        name="Acme Suite",
        m2m_ttl_s=300,
        is_active=True,
    )


def test_list_members_includes_has_grants(monkeypatch):
    app = _build_app(monkeypatch)
    members = [_app()]

    async def _members(_db, _rid):
        return members

    async def _has(_db, _svc):
        return True

    monkeypatch.setattr(admin_routes.realm_service, "list_members", _members)
    monkeypatch.setattr(admin_routes.realm_service, "service_app_has_grants", _has)
    resp = TestClient(app).get(f"/admin/realms/{uuid.uuid4()}/members")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["service_name"] == "docs"
    assert body[0]["has_grants"] is True


def test_add_member_standalone_app_succeeds_and_audits(monkeypatch):
    app = _build_app(monkeypatch)
    realm = _realm()
    candidate = _app(realm_id=None)

    async def _get_realm(_db, _rid):
        return realm

    async def _get_app(_db, _aid):
        return candidate

    async def _add(_db, _rid, _aid):
        return candidate

    async def _has(_db, _svc):
        return False

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.realm_service, "get_realm", _get_realm)
    monkeypatch.setattr(admin_routes.service_app_service, "get_service_app", _get_app)
    monkeypatch.setattr(admin_routes.realm_service, "add_member", _add)
    monkeypatch.setattr(admin_routes.realm_service, "service_app_has_grants", _has)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)

    resp = TestClient(app).post(
        f"/admin/realms/{realm.id}/members/{candidate.id}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 201
    assert resp.json()["has_grants"] is False
    assert logged["action"] == "realm_member_added"


def test_add_member_already_in_other_realm_409(monkeypatch):
    app = _build_app(monkeypatch)
    realm = _realm()
    other = _app(realm_id=uuid.uuid4())  # already in a DIFFERENT realm

    async def _get_realm(_db, _rid):
        return realm

    async def _get_app(_db, _aid):
        return other

    monkeypatch.setattr(admin_routes.realm_service, "get_realm", _get_realm)
    monkeypatch.setattr(admin_routes.service_app_service, "get_service_app", _get_app)

    resp = TestClient(app).post(
        f"/admin/realms/{realm.id}/members/{other.id}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 409


def test_add_member_realm_missing_404(monkeypatch):
    app = _build_app(monkeypatch)

    async def _get_realm(_db, _rid):
        return None

    monkeypatch.setattr(admin_routes.realm_service, "get_realm", _get_realm)
    resp = TestClient(app).post(
        f"/admin/realms/{uuid.uuid4()}/members/{uuid.uuid4()}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 404


def test_remove_member_204_and_audits(monkeypatch):
    app = _build_app(monkeypatch)
    realm_id = uuid.uuid4()
    member = _app(realm_id=realm_id)

    async def _get_app(_db, _aid):
        return member

    async def _remove(_db, _aid):
        return member

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.service_app_service, "get_service_app", _get_app)
    monkeypatch.setattr(admin_routes.realm_service, "remove_member", _remove)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)
    resp = TestClient(app).delete(
        f"/admin/realms/{realm_id}/members/{member.id}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 204
    assert logged["action"] == "realm_member_removed"


def test_remove_member_not_in_this_realm_404(monkeypatch):
    app = _build_app(monkeypatch)
    member = _app(realm_id=uuid.uuid4())  # belongs to a DIFFERENT realm

    async def _get_app(_db, _aid):
        return member

    monkeypatch.setattr(admin_routes.service_app_service, "get_service_app", _get_app)
    # path realm != member.realm_id
    resp = TestClient(app).delete(
        f"/admin/realms/{uuid.uuid4()}/members/{member.id}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 404


def test_realm_endpoints_require_admin_auth():
    # Router-level dependencies=[Depends(require_admin)] gates every /admin/* route,
    # including GET reads with no per-function dependency. Build the app withOUT
    # overriding require_admin: a request with no admin cookie must be rejected.
    app = FastAPI()
    app.include_router(admin_router)

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = (
        _db  # require_admin 401s on the missing cookie before db is used
    )
    resp = TestClient(app).get(f"/admin/realms/{uuid.uuid4()}/members")
    assert resp.status_code == 401
