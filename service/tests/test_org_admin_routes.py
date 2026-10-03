"""Org admin routes: guard + wiring tests with overridden deps (no real DB).

Mirrors tests/test_authz_resolve_guard.py: a minimal app, limiter disabled,
require_admin + get_db overridden, driven by TestClient.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api.dependencies import require_admin
from src.api.org_admin_routes import router as org_router
from src.database import get_db
import pytest

from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.services import org_admin_service


@pytest.fixture(autouse=True)
def _disable_limiter():
    """Disable the Redis-backed limiter for this module; restore after each test."""
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


_XRW = {"X-Requested-With": "XMLHttpRequest"}


class _Org:
    def __init__(self, is_public=False):
        self.id = uuid.uuid4()
        self.name = "Public" if is_public else "TAMU"
        self.slug = "public" if is_public else "tamu"
        self.is_public = is_public
        self.enabled = True


class _FakeDB:
    def __init__(self, get_results=None):
        self._get = list(get_results or [])

    async def get(self, _model, _pk):
        return self._get.pop(0) if self._get else None

    def add(self, _obj):
        pass

    async def delete(self, _obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass

    async def execute(self, _stmt):
        raise AssertionError("unexpected execute in this test")


def _app(db) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(org_router)
    app.dependency_overrides[require_admin] = lambda: {
        "sub": str(uuid.uuid4()),
        "admin": True,
    }

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return app


def test_delete_public_org_returns_400(monkeypatch):
    pub = _Org(is_public=True)
    client = TestClient(_app(_FakeDB(get_results=[pub])))
    resp = client.delete(f"/admin/organizations/{pub.id}", headers=_XRW)
    assert resp.status_code == 400
    assert "public" in resp.json()["detail"].lower()


def test_delete_missing_org_returns_404():
    client = TestClient(_app(_FakeDB(get_results=[None])))
    resp = client.delete(f"/admin/organizations/{uuid.uuid4()}", headers=_XRW)
    assert resp.status_code == 404


def test_create_org_duplicate_slug_returns_409(monkeypatch):
    async def _boom(db, name, slug, created_by=None):
        raise org_admin_service.OrgConflict("slug taken")

    monkeypatch.setattr(org_admin_service, "create_organization", _boom)
    client = TestClient(_app(_FakeDB()))
    resp = client.post(
        "/admin/organizations",
        json={"name": "TAMU", "slug": "tamu"},
        headers=_XRW,
    )
    assert resp.status_code == 409


def test_create_org_ok_returns_201(monkeypatch):
    org = _Org()

    async def _ok(db, name, slug, created_by=None):
        return org

    monkeypatch.setattr(org_admin_service, "create_organization", _ok)
    client = TestClient(_app(_FakeDB()))
    resp = client.post(
        "/admin/organizations",
        json={"name": "TAMU", "slug": "tamu"},
        headers=_XRW,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "tamu"
    assert body["domain_count"] == 0


def test_set_allowed_orgs_unknown_id_returns_400(monkeypatch):
    async def _boom(db, ws, ids):
        raise ValueError("Unknown organization ids: ['x']")

    monkeypatch.setattr(org_admin_service, "set_workspace_allowed_orgs", _boom)
    client = TestClient(_app(_FakeDB()))
    resp = client.put(
        f"/admin/workspaces/{uuid.uuid4()}/allowed-organizations",
        json={"organization_ids": [str(uuid.uuid4())]},
        headers=_XRW,
    )
    assert resp.status_code == 400


def test_list_org_users_unknown_org_returns_404():
    client = TestClient(_app(_FakeDB(get_results=[None])))
    resp = client.get(f"/admin/organizations/{uuid.uuid4()}/users")
    assert resp.status_code == 404


def test_list_org_users_invalid_page_returns_422():
    # ge=1 bound rejects page=0 at validation time, before any DB access.
    client = TestClient(_app(_FakeDB()))
    resp = client.get(f"/admin/organizations/{uuid.uuid4()}/users?page=0")
    assert resp.status_code == 422
