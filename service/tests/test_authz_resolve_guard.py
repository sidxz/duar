"""V5 regression test: /authz/resolve must not mint tokens for Origin-authenticated callers.

Minting an authz JWT is a credential-issuance step. Only callers holding a
valid ``X-Service-Key`` may trigger it; browsers using Origin-header auth are
allowed to discover workspaces but must route the mint through a backend.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api.authz_routes import router as authz_router
from src.api.dependencies import ServiceKeyContext, require_service_context
from src.database import get_db
import pytest

from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler


@pytest.fixture(autouse=True)
def _disable_limiter():
    """Disable Redis-backed limiter for this module.

    The guard under test fires before any rate-limit store is consulted, so
    connecting to Redis is unnecessary noise. Restore the original value after
    each test so the singleton is not permanently mutated for later test files.
    """
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


def _make_app(origin_authenticated: bool) -> FastAPI:
    """Minimal FastAPI app wiring the authz router + limiter state.

    Overrides ``require_service_context`` to simulate the desired caller type
    (origin-authed vs service-key-authed) and ``get_db`` to a no-op — the guard
    fires before any DB access so the real session is not needed.
    """
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(authz_router)

    def _ctx() -> ServiceKeyContext:
        return ServiceKeyContext(
            service_name="test-svc", origin_authenticated=origin_authenticated
        )

    async def _db():
        yield None

    app.dependency_overrides[require_service_context] = _ctx
    app.dependency_overrides[get_db] = _db
    return app


class TestAuthzResolveGuard:
    def test_origin_auth_mint_attempt_is_rejected(self):
        app = _make_app(origin_authenticated=True)
        client = TestClient(app)
        resp = client.post(
            "/authz/resolve",
            json={
                "idp_token": "unused-guard-fires-first",
                "provider": "google",
                "workspace_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 403
        assert "service key" in resp.json()["detail"].lower()

    def test_origin_auth_discovery_is_allowed_past_guard(self):
        """Origin-auth without a workspace_id must pass the guard (whatever happens
        later is tested by other cases; here we just prove the guard doesn't fire)."""
        app = _make_app(origin_authenticated=True)
        client = TestClient(app)
        resp = client.post(
            "/authz/resolve",
            json={"idp_token": "dummy", "provider": "google"},
        )
        # The guard lets this through; the next step (IdP validation) will 400/500
        # because the token is bogus. Any status OTHER than 403-with-service-key
        # detail proves the guard passed.
        if resp.status_code == 403:
            assert "service key" not in resp.json().get("detail", "").lower()

    def test_service_key_mint_passes_guard(self):
        """Service-key callers are allowed to mint (guard does not fire)."""
        app = _make_app(origin_authenticated=False)
        client = TestClient(app)
        resp = client.post(
            "/authz/resolve",
            json={
                "idp_token": "dummy",
                "provider": "google",
                "workspace_id": str(uuid.uuid4()),
            },
        )
        # Guard lets this through. Downstream validation rejects the bogus
        # IdP token — but not with the guard's "service key required" message.
        if resp.status_code == 403:
            assert "service key" not in resp.json().get("detail", "").lower()
