"""Behavioral tests for /realm/whoami + /realm/m2m-token.

Drives the real handlers through a TestClient with the service-key dependency and
the realm lookup mocked — the house behavioral style (see test_realm_authz_minting).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api import realm_routes
from src.api.dependencies import ServiceKeyContext, require_service_key
from src.api.realm_routes import router as realm_router
from src.auth.jwt import _AUD_M2M, decode_token
from src.database import get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


class _Realm:
    def __init__(
        self, *, slug="acme-suite", name="Acme Suite", m2m_ttl_s=300, is_active=True
    ):
        self.slug = slug
        self.name = name
        self.m2m_ttl_s = m2m_ttl_s
        self.is_active = is_active


def _build_app(*, realm_slug):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(realm_router)
    app.dependency_overrides[require_service_key] = lambda: ServiceKeyContext(
        service_name="docs", realm_slug=realm_slug
    )

    async def _db():
        yield None

    app.dependency_overrides[get_db] = _db
    return app


def test_whoami_member_returns_realm_and_scope(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug, name="Acme Suite")

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    resp = TestClient(_build_app(realm_slug="acme-suite")).get("/realm/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service_name"] == "docs"
    assert body["effective_scope"] == "acme-suite"
    assert body["realm"] == {"slug": "acme-suite", "name": "Acme Suite"}


def test_whoami_standalone_has_null_realm():
    resp = TestClient(_build_app(realm_slug=None)).get("/realm/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service_name"] == "docs"
    assert body["effective_scope"] == "docs"
    assert body["realm"] is None


def test_whoami_orphan_realm_returns_slug_scope_and_null_realm(monkeypatch):
    async def _none(_db, _slug):
        return None

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _none)
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug="acme-suite")).get("/realm/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["effective_scope"] == "acme-suite"  # still operating under the slug
    assert body["realm"] is None  # graceful, not a crash


def test_m2m_mint_stamps_caller_and_realm_svc(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug, m2m_ttl_s=300, is_active=True)

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug="acme-suite")).post(
        "/realm/m2m-token", json={}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["expires_in"] == 300
    payload = decode_token(body["token"], audience=_AUD_M2M)
    assert payload["type"] == "m2m"
    assert payload["svc"] == "acme-suite"  # shared realm scope (any member accepts)
    assert payload["caller"] == "docs"  # server-stamped minter, not client-asserted
    assert payload["actions"] == ["*"]
    assert "sub" not in payload


def test_m2m_mint_rejects_standalone(monkeypatch):
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug=None)).post("/realm/m2m-token", json={})
    assert resp.status_code == 403


def test_m2m_mint_rejects_inactive_realm(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug, is_active=False)

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug="acme-suite")).post(
        "/realm/m2m-token", json={}
    )
    assert resp.status_code == 403
