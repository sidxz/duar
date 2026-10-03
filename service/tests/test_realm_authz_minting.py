"""/authz/resolve binds the minted authz token's ``svc`` claim — and the RBAC
actions lookup — to the caller's ``effective_scope``: the realm slug for a realm
member, the service's own name for a standalone caller.

Behavioral test: it drives the real ``/authz/resolve`` handler through a
TestClient with IdP validation + provisioning mocked, the same scaffolding as
``test_authz_org_gate.py``. The realm-member case is the RED→GREEN driver (before
the Task-5 change the handler stamps ``service_ctx.service_name``, so ``svc`` is
the caller's own name, not the realm slug); the standalone case is a
non-breaking regression guard.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api import authz_routes
from src.api.authz_routes import router as authz_router
from src.api.dependencies import ServiceKeyContext, require_service_context
from src.auth.jwt import _AUD_AUTHZ, decode_token
from src.database import get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


_CLAIMS = {"sub": "google|1", "email": "alice@tamu.edu", "name": "Alice"}


class _Org:
    def __init__(self):
        self.id = uuid.uuid4()
        self.slug = "tamu"
        self.is_public = False


class _User:
    def __init__(self, org_id):
        self.id = uuid.uuid4()
        self.email = "alice@tamu.edu"
        self.name = "Alice"
        self.is_active = True
        self.organization_id = org_id


class _MembershipResult:
    def scalar_one_or_none(self):
        return type("M", (), {"role": "editor"})()


class _FakeDB:
    """execute() -> the workspace membership row; get() -> the workspace."""

    def __init__(self, workspace):
        self._workspace = workspace

    async def execute(self, _stmt):
        return _MembershipResult()

    async def get(self, _model, _pk):
        return self._workspace


async def _fake_validate(token, provider, expected_nonce=None, expected_audiences=None):
    return _CLAIMS


def _build_app(db, *, realm_slug):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(authz_router)
    app.dependency_overrides[require_service_context] = lambda: ServiceKeyContext(
        service_name="docs", origin_authenticated=False, realm_slug=realm_slug
    )

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return app


def _wire_mocks(monkeypatch, recorded):
    org = _Org()
    user = _User(org.id)
    monkeypatch.setattr(authz_routes, "validate_idp_token", _fake_validate)
    # Audit side-effects are not under test; no-op them so the bare TestClient app
    # (no RequestContext middleware) doesn't trip over request-context lookups.
    monkeypatch.setattr(authz_routes, "bind_identity", lambda *a, **k: None)
    monkeypatch.setattr(authz_routes, "log_security", lambda *a, **k: None)

    async def _resolve(_db, _email):
        return org

    monkeypatch.setattr(
        authz_routes.organization_service, "resolve_organization", _resolve
    )

    async def _foc(**kwargs):
        return user

    monkeypatch.setattr(authz_routes.auth_service, "find_or_create_user", _foc)

    async def _allow(_db, _ws, _org_id):
        return True

    monkeypatch.setattr(
        authz_routes.organization_service, "workspace_allows_org", _allow
    )
    monkeypatch.setattr(
        authz_routes.organization_service,
        "org_claims",
        lambda _org: {"org_id": "oid", "org_slug": "tamu", "org_is_public": False},
    )

    async def _actions(_db, _user_id, service_name, _ws_id):
        recorded["scope"] = service_name
        return ["reports:export"]

    monkeypatch.setattr(authz_routes, "get_user_actions", _actions)


def _post(app):
    client = TestClient(app)
    return client.post(
        "/authz/resolve",
        json={
            "idp_token": "x",
            "provider": "google",
            "workspace_id": str(uuid.uuid4()),
        },
    )


def _workspace():
    return type("W", (), {"id": uuid.uuid4(), "slug": "w", "name": "W"})()


def test_realm_member_token_svc_is_realm_slug(monkeypatch):
    recorded: dict = {}
    _wire_mocks(monkeypatch, recorded)
    resp = _post(_build_app(_FakeDB(_workspace()), realm_slug="acme-suite"))
    assert resp.status_code == 200
    payload = decode_token(resp.json()["authz_token"], audience=_AUD_AUTHZ)
    # Token is honored by ANY realm member, so svc = the shared realm slug.
    assert payload["svc"] == "acme-suite"
    # RBAC actions resolved under the shared realm scope, not the caller's own name.
    assert recorded["scope"] == "acme-suite"


def test_standalone_token_svc_is_service_name(monkeypatch):
    recorded: dict = {}
    _wire_mocks(monkeypatch, recorded)
    resp = _post(_build_app(_FakeDB(_workspace()), realm_slug=None))
    assert resp.status_code == 200
    payload = decode_token(resp.json()["authz_token"], audience=_AUD_AUTHZ)
    # Non-breaking: a standalone caller's scope is its own service_name.
    assert payload["svc"] == "docs"
    assert recorded["scope"] == "docs"
