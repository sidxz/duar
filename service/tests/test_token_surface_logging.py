"""Token-surface security events: issuance, rejects, and fail-closed revocation.

Pins the tranche-A log-coverage fixes — every credential mint/deny on the
token surface must emit a category=security event:
- auth.token.issued on every access+refresh pair (with family_id)
- auth.token.refresh_rejected on malformed/expired refresh tokens
- auth.token.family_revoked (stream + activity row) on fail-closed revocation
- auth.login.rejected on the (client_id, redirect_uri) allowlist probe
- realm.m2m.denied on both leaked-key mint-attempt 403s
- authz.token.denied when an Origin-authenticated caller tries to mint
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware
from structlog.testing import capture_logs

from src.api.dependencies import (
    ServiceKeyContext,
    require_service_context,
    require_service_key,
)
from src.database import get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.models.activity import ActivityLog
from src.services import auth_service

WORKSPACE_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


def _write_keypair(tmp_path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path = tmp_path / "private.pem"
    pub_path = tmp_path / "public.pem"
    priv_path.write_bytes(priv)
    pub_path.write_bytes(pub)
    return priv_path, pub_path


@pytest.fixture(autouse=True)
def _ephemeral_keys(tmp_path, monkeypatch):
    from src.auth import key_provider
    from src.config import settings

    priv_path, pub_path = _write_keypair(tmp_path)
    monkeypatch.setattr(settings, "jwt_private_key_path", priv_path)
    monkeypatch.setattr(settings, "jwt_public_key_path", pub_path)
    key_provider.reset_cache()
    yield
    key_provider.reset_cache()


def _fake_user() -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.name = "Test User"
    user.is_active = True
    user.organization_id = None
    return user


def _events(logs, name):
    return [e for e in logs if e.get("event") == name]


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_tokens_emits_issued_event():
    user = _fake_user()

    membership = MagicMock()
    membership.role = "editor"
    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = membership

    allows_org_scalars = MagicMock()
    allows_org_scalars.all.return_value = []
    allows_org_result = MagicMock()
    allows_org_result.scalars.return_value = allows_org_scalars

    groups_result = MagicMock()
    groups_result.all.return_value = []

    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[membership_result, allows_org_result, groups_result]
    )

    stored: dict = {}

    async def fake_store(**kwargs):
        stored.update(kwargs)

    client_app_id = uuid.uuid4()
    with (
        patch(
            "src.services.auth_service.token_service.store_refresh_token",
            new=fake_store,
        ),
        capture_logs() as logs,
    ):
        await auth_service.issue_tokens(
            db, user, WORKSPACE_ID, "test-ws", client_app_id=client_app_id
        )

    events = _events(logs, "auth.token.issued")
    assert events, "token issuance must emit auth.token.issued"
    evt = events[0]
    assert evt["outcome"] == "success"
    assert evt["category"] == "security"
    assert evt["actor"] == str(user.id)
    assert evt["workspace_id"] == str(WORKSPACE_ID)
    assert evt["client_app_id"] == str(client_app_id)
    # the correlation key for later reuse/revocation events
    assert evt["family_id"] == stored["family_id"]


@pytest.mark.asyncio
async def test_invalid_refresh_token_emits_refresh_rejected():
    with capture_logs() as logs:
        with pytest.raises(ValueError, match="Invalid refresh token"):
            await auth_service.rotate_refresh_token(MagicMock(), "not-a-jwt")

    events = _events(logs, "auth.token.refresh_rejected")
    assert events and events[0]["outcome"] == "denied"
    assert events[0]["reason"] == "invalid_token"


class _FakeDBInactiveUser:
    """db.get returns None (user gone) → rotation fails closed."""

    sync_session = None

    def __init__(self):
        self.added = []
        self.committed = False

    async def get(self, model, pk):
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def rollback(self):
        pass

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_fail_closed_family_revocation_logs_both_channels():
    from src.auth.jwt import create_refresh_token

    user_id = uuid.uuid4()
    family_id = str(uuid.uuid4())
    refresh_token = create_refresh_token(user_id=user_id, family_id=family_id)
    db = _FakeDBInactiveUser()
    revoked: list = []

    async def fake_consume(_jti):
        return (user_id, family_id, WORKSPACE_ID, None)

    async def fake_revoke(fid):
        revoked.append(fid)

    with (
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=fake_consume,
        ),
        patch(
            "src.services.auth_service.token_service.revoke_token_family",
            new=fake_revoke,
        ),
        capture_logs() as logs,
    ):
        with pytest.raises(ValueError, match="User not found or inactive"):
            await auth_service.rotate_refresh_token(db, refresh_token)

    assert revoked == [family_id]
    events = _events(logs, "auth.token.family_revoked")
    assert events, "fail-closed family revocation must emit a security event"
    evt = events[0]
    assert evt["outcome"] == "denied"
    assert evt["actor"] == str(user_id)
    assert evt["family_id"] == family_id
    # and the admin-visible activity row
    rows = [r for r in db.added if isinstance(r, ActivityLog)]
    assert rows and rows[0].action == "token_family_revoked"
    assert rows[0].target_id == user_id
    assert rows[0].detail["family_id"] == family_id
    assert db.committed


# ---------------------------------------------------------------------------
# Route layer
# ---------------------------------------------------------------------------


def _base_app() -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    return app


def test_login_allowlist_reject_emits_event(monkeypatch):
    from src.api import auth_routes

    monkeypatch.setattr(auth_routes, "get_configured_providers", lambda: ["google"])

    app = _base_app()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(auth_routes.router)

    no_app_result = MagicMock()
    no_app_result.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute = AsyncMock(return_value=no_app_result)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db

    client_id = uuid.uuid4()
    with capture_logs() as logs:
        resp = TestClient(app).get(
            "/auth/login/google",
            params={
                "client_id": str(client_id),
                "redirect_uri": "https://evil.example/cb",
                "code_challenge": "x" * 43,
            },
        )
    assert resp.status_code == 400
    events = _events(logs, "auth.login.rejected")
    assert events, "allowlist probe must emit auth.login.rejected"
    evt = events[0]
    assert evt["reason"] == "redirect_uri_not_allowed"
    assert evt["client_id"] == str(client_id)
    assert evt["redirect_uri"] == "https://evil.example/cb"


@pytest.mark.parametrize(
    ("realm_slug", "reason"),
    [(None, "not_a_realm_member"), ("dead-realm", "realm_inactive")],
)
def test_m2m_mint_denied_emits_event(monkeypatch, realm_slug, reason):
    from src.api import realm_routes

    app = _base_app()
    app.include_router(realm_routes.router)
    app.dependency_overrides[require_service_key] = lambda: ServiceKeyContext(
        service_name="notes", realm_slug=realm_slug
    )

    async def _db():
        yield None

    app.dependency_overrides[get_db] = _db

    async def fake_get_realm(_db, _slug):
        return None

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", fake_get_realm)

    with capture_logs() as logs:
        resp = TestClient(app).post("/realm/m2m-token", json={})
    assert resp.status_code == 403
    events = _events(logs, "realm.m2m.denied")
    assert events and events[0]["reason"] == reason
    assert events[0]["caller_service"] == "notes"


def test_resolve_origin_mint_attempt_emits_event():
    from src.api import authz_routes

    app = _base_app()
    app.include_router(authz_routes.router)
    app.dependency_overrides[require_service_context] = lambda: ServiceKeyContext(
        service_name="notes", origin_authenticated=True
    )

    async def _db():
        yield None

    app.dependency_overrides[get_db] = _db

    with capture_logs() as logs:
        resp = TestClient(app).post(
            "/authz/resolve",
            json={
                "idp_token": "tok",
                "provider": "google",
                "workspace_id": str(WORKSPACE_ID),
            },
        )
    assert resp.status_code == 403
    events = _events(logs, "authz.token.denied")
    assert events and events[0]["reason"] == "origin_auth_cannot_mint"
    assert events[0]["caller_service"] == "notes"
