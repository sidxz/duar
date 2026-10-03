"""Regression: /authz/resolve must bind the IdP token's audience to the CALLING app.

The id_token's audience is otherwise checked only against the single,
deployment-wide provider client_id. So a token minted for app A (aud=clientA)
mints just fine when presented via app B's service key — a stolen id_token works
at *any* app's mint route on the deployment. This binds the token to the calling
app's registered IdP audience(s): app B's service (registered for clientB) must
reject a token carrying clientA, even though it is otherwise valid.

The audience check runs inside `validate_idp_token` (step 1 of resolve), before
any DB access — so this exercises the real route with a no-op DB.
"""

from __future__ import annotations

import time
import uuid

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api.authz_routes import router as authz_router
from src.api.dependencies import ServiceKeyContext, require_service_context
from src.database import get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler

# The mint guard / audience check run before any rate-limit state is consulted.
# Use a per-test fixture (not module-level mutation) so the singleton is restored.


@pytest.fixture(autouse=True)
def _disable_limiter():
    """Disable the Redis-backed limiter for this module; restore after each test."""
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


_ISSUER = "https://rogue.test"
_DEPLOYMENT_AUDIENCE = "deployment-wide-client"


@pytest.fixture(scope="module")
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key()


@pytest.fixture
def test_oidc_static(rsa_keypair, monkeypatch):
    """Register the gated static-key test_oidc provider with a known deployment audience."""
    from src.services import idp_validator

    _, public_key = rsa_keypair
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setenv("TEST_TRUSTED_ISSUER_PUBKEY", pub_pem)
    monkeypatch.setenv("TEST_TRUSTED_ISSUER", _ISSUER)
    monkeypatch.setenv("TEST_TRUSTED_AUDIENCE", _DEPLOYMENT_AUDIENCE)
    idp_validator._register_test_provider()
    try:
        yield
    finally:
        idp_validator._PROVIDER_CONFIG.pop("test_oidc", None)


def _token(rsa_keypair, *, aud: str) -> str:
    private_key, _ = rsa_keypair
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": "u1",
            "email": "u1@x.test",
            "email_verified": True,
            "iss": _ISSUER,
            "aud": aud,
            "iat": now,
            "exp": now + 3600,
        },
        pem,
        algorithm="RS256",
    )


def _make_app(allowed_idp_audiences: tuple[str, ...]) -> FastAPI:
    """Service-key caller registered to accept ``allowed_idp_audiences``. DB is a no-op:
    the audience check rejects before any DB access."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(authz_router)

    def _ctx() -> ServiceKeyContext:
        return ServiceKeyContext(
            service_name="app-b",
            origin_authenticated=False,
            allowed_idp_audiences=allowed_idp_audiences,
        )

    async def _db():
        yield None

    app.dependency_overrides[require_service_context] = _ctx
    app.dependency_overrides[get_db] = _db
    return app


def test_token_for_another_app_is_rejected(rsa_keypair, test_oidc_static):
    """app B's service (registered for clientB) rejects a token carrying the
    deployment-wide audience that is NOT clientB — closes cross-app replay."""
    app = _make_app(allowed_idp_audiences=("app-b-client",))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/authz/resolve",
        json={
            "idp_token": _token(rsa_keypair, aud=_DEPLOYMENT_AUDIENCE),
            "provider": "test_oidc",
            "workspace_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 400
    assert "token" in resp.json()["detail"].lower()


def test_token_for_the_calling_app_passes_audience_check(rsa_keypair, test_oidc_static):
    """When the token's aud is the calling app's registered audience, the audience check
    passes (validation proceeds past step 1 — it does NOT 400 on the token)."""
    app = _make_app(allowed_idp_audiences=(_DEPLOYMENT_AUDIENCE,))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/authz/resolve",
        json={
            "idp_token": _token(rsa_keypair, aud=_DEPLOYMENT_AUDIENCE),
            "provider": "test_oidc",
            "workspace_id": str(uuid.uuid4()),
        },
    )
    # Past the audience check the no-op DB makes the call fail downstream (500),
    # but it must NOT be a 400 IdP-token rejection.
    assert resp.status_code != 400
