"""Regression test: ``/authz/idp/github/callback`` must validate the OAuth
``state`` parameter against the session value stored at login start.

Vulnerability: ``idp_login`` generated ``state=uuid4().hex`` but never stored
it in the session, and ``idp_callback`` did not accept or validate ``state``.
This meant the GitHub-proxy AuthZ flow had no CSRF protection on the callback;
an attacker could pre-mint a GitHub code, force-populate a victim's session
via a crafted login URL, and trigger the callback to exchange the attacker's
code under the victim's session. Proxy mode's ``/auth/callback`` validates
state via Authlib — this test pins parity with that behavior for the
AuthZ-mode GitHub proxy.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from src.api.authz_routes import idp_router as authz_idp_router
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


def _make_app() -> FastAPI:
    """App with session middleware + a test-only helper to seed session values.

    The helper lets tests simulate the post-login state (session populated with
    redirect_uri, nonce, and state) without having to go through the real
    login endpoint, which would require a registered ServiceApp row in the DB.
    """
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-tests")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(authz_idp_router)

    async def _db():
        # _validate_authz_redirect_uri runs an origin allowlist query — we
        # override it per-test below when we need it to pass.
        yield None

    app.dependency_overrides[get_db] = _db

    @app.get("/_test/seed")
    def seed(
        request: Request,
        redirect_uri: str | None = None,
        nonce: str | None = None,
        state: str | None = None,
    ):
        if redirect_uri is not None:
            request.session["authz_idp_redirect_uri"] = redirect_uri
        if nonce is not None:
            request.session["authz_idp_nonce"] = nonce
        if state is not None:
            request.session["authz_idp_state"] = state
        return {"ok": True}

    return app


class TestGithubCallbackStateValidation:
    def test_missing_state_query_param_rejected(self):
        app = _make_app()
        client = TestClient(app)
        client.get(
            "/_test/seed?redirect_uri=https://legit.example.com/cb"
            "&nonce=n&state=correct"
        )
        resp = client.get("/authz/idp/github/callback?code=x", follow_redirects=False)
        assert resp.status_code == 400
        assert "state" in resp.json()["detail"].lower()

    def test_state_mismatch_rejected(self):
        app = _make_app()
        client = TestClient(app)
        client.get(
            "/_test/seed?redirect_uri=https://legit.example.com/cb"
            "&nonce=n&state=correct-server-state"
        )
        resp = client.get(
            "/authz/idp/github/callback?code=x&state=attacker-state",
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert "state" in resp.json()["detail"].lower()

    def test_missing_state_in_session_rejected(self):
        """If the session has no stored state, the callback must reject —
        this covers the attacker-crafted-callback-without-prior-login path."""
        app = _make_app()
        client = TestClient(app)
        # Seed redirect_uri + nonce but NOT state (simulates tampered session
        # or direct callback hit without starting login)
        client.get("/_test/seed?redirect_uri=https://legit.example.com/cb&nonce=n")
        resp = client.get(
            "/authz/idp/github/callback?code=x&state=anything",
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert "state" in resp.json()["detail"].lower()
