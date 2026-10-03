"""/authz/resolve must apply the org sign-in gate AND the workspace allowed-orgs
enforcement, and must thread organization_id through JIT provisioning.

Regression: making find_or_create_user's organization_id required previously
broke this route with a TypeError that no test caught (downstream IdP validation
rejected bogus test tokens before reaching the call). These tests stub IdP
validation and provisioning so the org wiring is exercised directly.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api import authz_routes
from src.api.authz_routes import router as authz_router
from src.api.dependencies import ServiceKeyContext, require_service_context
from src.database import get_db
import pytest

from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler


@pytest.fixture(autouse=True)
def _disable_limiter():
    """Disable the Redis-backed limiter for this module; restore after each test."""
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


_CLAIMS = {"sub": "google|1", "email": "alice@tamu.edu", "name": "Alice"}


class _Org:
    def __init__(self, is_public: bool = False):
        self.id = uuid.uuid4()
        self.slug = "tamu"
        self.is_public = is_public


class _User:
    def __init__(self, org_id):
        self.id = uuid.uuid4()
        self.email = "alice@tamu.edu"
        self.name = "Alice"
        self.is_active = True
        self.organization_id = org_id


class _Result:
    def __init__(self, *, all_rows=None, scalar=None):
        self._all = all_rows or []
        self._scalar = scalar

    def all(self):
        return self._all

    def scalar_one_or_none(self):
        return self._scalar


class _FakeDB:
    def __init__(self, execute_results=None):
        self._exec = list(execute_results or [])

    async def execute(self, _stmt):
        return self._exec.pop(0)

    async def get(self, _model, _pk):
        return None


def _app(db) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(authz_router)
    app.dependency_overrides[require_service_context] = lambda: ServiceKeyContext(
        service_name="svc", origin_authenticated=False
    )

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return app


async def _fake_validate(token, provider, expected_nonce=None, expected_audiences=None):
    return _CLAIMS


def test_authz_gate_rejects_unresolved_org(monkeypatch):
    monkeypatch.setattr(authz_routes, "validate_idp_token", _fake_validate)

    async def _resolve_none(_db, _email):
        return None

    monkeypatch.setattr(
        authz_routes.organization_service, "resolve_organization", _resolve_none
    )
    client = TestClient(_app(_FakeDB()))
    resp = client.post(
        "/authz/resolve",
        json={
            "idp_token": "x",
            "provider": "google",
            "workspace_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 403
    assert "domain" in resp.json()["detail"].lower()


def test_authz_passes_org_through_provisioning(monkeypatch):
    """Regression guard: the route must pass organization_id to find_or_create_user."""
    org = _Org()
    recorded: dict = {}
    monkeypatch.setattr(authz_routes, "validate_idp_token", _fake_validate)

    async def _resolve(_db, _email):
        return org

    monkeypatch.setattr(
        authz_routes.organization_service, "resolve_organization", _resolve
    )

    async def _foc(**kwargs):
        recorded.update(kwargs)
        return _User(kwargs["organization_id"])

    monkeypatch.setattr(authz_routes.auth_service, "find_or_create_user", _foc)

    # Discovery path (no workspace_id): one execute() for the workspace list.
    db = _FakeDB(execute_results=[_Result(all_rows=[])])
    client = TestClient(_app(db))
    resp = client.post("/authz/resolve", json={"idp_token": "x", "provider": "google"})
    assert resp.status_code == 200
    assert recorded["organization_id"] == org.id


def test_authz_enforces_workspace_allowed_orgs(monkeypatch):
    org = _Org()
    user = _User(org.id)
    monkeypatch.setattr(authz_routes, "validate_idp_token", _fake_validate)

    async def _resolve(_db, _email):
        return org

    monkeypatch.setattr(
        authz_routes.organization_service, "resolve_organization", _resolve
    )

    async def _foc(**_kwargs):
        return user

    monkeypatch.setattr(authz_routes.auth_service, "find_or_create_user", _foc)

    async def _disallow(_db, _ws, _org_id):
        return False

    monkeypatch.setattr(
        authz_routes.organization_service, "workspace_allows_org", _disallow
    )

    membership = type("M", (), {"role": "editor"})()
    db = _FakeDB(execute_results=[_Result(scalar=membership)])
    client = TestClient(_app(db))
    resp = client.post(
        "/authz/resolve",
        json={
            "idp_token": "x",
            "provider": "google",
            "workspace_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 403
    assert "organization is not permitted" in resp.json()["detail"].lower()
