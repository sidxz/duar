# service/tests/integration/test_realm_flow_b.py
"""Flow B end-to-end: the REAL /realm/m2m-token mint accepted by the REAL SDK verifier.

In-process (TestClient = httpx-over-ASGI, no socket). The realm DB layer + service-key
dependency are faked the same way as tests/test_realm_routes.py; the token itself is
REAL (signed by the ambient key_provider), and the SDK does REAL RS256 verification.
"""

import json
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from duar_auth import Duar, SystemAuth
from duar_auth.types import DuarError
from src.api import realm_routes
from src.api.dependencies import ServiceKeyContext, require_service_key
from src.api.realm_routes import router as realm_router
from src.auth import key_provider
from src.auth.jwt import create_authz_token, create_m2m_token
from src.database import get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler

_FIX = json.loads((Path(__file__).parent / "fixtures" / "fixtures.json").read_text())


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
        service_name="app-a", realm_slug=realm_slug
    )

    async def _db():
        yield None

    app.dependency_overrides[get_db] = _db
    return app


def _pubpem_for(token: str) -> str:
    """The verifying public PEM for a real token, by its kid (ambient signing key)."""
    kid = pyjwt.get_unverified_header(token)["kid"]
    return key_provider.verification_keys()[kid]


def _sdk(*, effective_scope: str, public_pem: str, service_name: str = "app-b") -> Duar:
    s = Duar(
        base_url="https://duar.test",
        service_name=service_name,
        service_key="svc-key",
        idp_public_key="-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----",
        idp_audience="client-id",
    )
    s._duar_public_key = public_pem
    s._effective_scope = effective_scope
    return s


def test_flow_b_real_mint_accepted_by_sdk(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug, m2m_ttl_s=300, is_active=True)

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)

    resp = TestClient(_build_app(realm_slug="acme-suite")).post(
        "/realm/m2m-token", json={}
    )
    assert resp.status_code == 200
    token = resp.json()["token"]

    sdk = _sdk(effective_scope="acme-suite", public_pem=_pubpem_for(token))
    sys_auth = sdk.verify_m2m_token(token)
    assert isinstance(sys_auth, SystemAuth)
    assert sys_auth.caller == "app-a"  # server-stamped from the authenticated key
    assert sys_auth.svc == "acme-suite"
    assert sys_auth.can("anything") is True


def test_flow_b_wrong_realm_rejected(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug)

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug="acme-suite")).post(
        "/realm/m2m-token", json={}
    )
    token = resp.json()["token"]
    # Receiver's effective_scope is a DIFFERENT realm → cross-realm replay must fail.
    sdk = _sdk(effective_scope="other-realm", public_pem=_pubpem_for(token))
    with pytest.raises(DuarError) as exc:
        sdk.verify_m2m_token(token)
    assert exc.value.status_code == 403


def test_flow_b_non_member_cannot_mint(monkeypatch):
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug=None)).post("/realm/m2m-token", json={})
    assert resp.status_code == 403  # standalone service has no realm to mint under


def test_flow_b_expired_rejected():
    token = create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=-10)
    sdk = _sdk(effective_scope="acme-suite", public_pem=_pubpem_for(token))
    with pytest.raises(DuarError) as exc:
        sdk.verify_m2m_token(token)
    assert exc.value.status_code == 401


def test_flow_b_authz_token_rejected_as_m2m():
    # Token-type-confusion defense: a real authz token (aud=duar:authz) must never
    # validate through the m2m verifier (aud=duar:m2m).
    import uuid

    token = create_authz_token(
        user_id=uuid.UUID(int=1),
        idp_sub="google|1",
        workspace_id=uuid.UUID(int=2),
        workspace_slug="acme",
        workspace_role="editor",
        actions=["read"],
        service_name="acme-suite",
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )
    sdk = _sdk(effective_scope="acme-suite", public_pem=_pubpem_for(token))
    with pytest.raises(DuarError) as exc:
        sdk.verify_m2m_token(token)
    assert (
        exc.value.status_code == 401
    )  # wrong audience → bad token, not a scope (403) error


def test_flow_b_aud_target_mismatch_rejected():
    token = create_m2m_token(
        svc="acme-suite", caller="app-a", ttl_s=300, aud_target="billing"
    )
    # Receiver is "reports", token targets "billing" → reject.
    sdk = _sdk(
        effective_scope="acme-suite",
        public_pem=_pubpem_for(token),
        service_name="reports",
    )
    with pytest.raises(DuarError) as exc:
        sdk.verify_m2m_token(token)
    assert exc.value.status_code == 403


def test_committed_fixtures_accepted_and_negatives_rejected():
    """The committed fixtures.json the JS test consumes still verifies under the SDK."""
    sdk = _sdk(
        effective_scope="acme-suite",
        public_pem=_FIX["public_pem"],
        service_name="reports",
    )
    ok = sdk.verify_m2m_token(_FIX["tokens"]["m2m_valid"])
    assert ok.svc == "acme-suite"
    assert ok.caller == "app-a"
    expected = {
        "m2m_expired": 401,  # expired signature → bad token
        "m2m_wrong_realm": 403,  # svc != effective_scope → scope mismatch
        "authz_valid": 401,  # wrong audience (duar:authz) → bad token
        "m2m_aud_target": 403,  # aud_target targets a different service
    }
    for label, code in expected.items():
        with pytest.raises(DuarError) as exc:
            sdk.verify_m2m_token(_FIX["tokens"][label])
        assert exc.value.status_code == code


def test_committed_m2m_claims_match_current_minter():
    """Freshness guard: if the service minter's claim set drifts and fixtures.json is
    not regenerated, this fails — keeping the JS vector honest. Compares claim KEYS
    (and aud/type), not volatile values (iat/exp/jti)."""
    committed = pyjwt.decode(
        _FIX["tokens"]["m2m_valid"],
        _FIX["public_pem"],
        algorithms=["RS256"],
        audience="duar:m2m",
    )
    fresh_token = create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=300)
    fresh = pyjwt.decode(
        fresh_token,
        _pubpem_for(fresh_token),
        algorithms=["RS256"],
        audience="duar:m2m",
    )
    assert sorted(committed.keys()) == sorted(fresh.keys())
    assert committed["aud"] == fresh["aud"] == "duar:m2m"
    assert committed["type"] == fresh["type"] == "m2m"
    # Value-shape too, not just claim names: a drift that retypes a claim the JS
    # vector relies on (e.g. actions list -> CSV string) without renaming it would
    # otherwise leave the committed fixtures silently stale.
    assert fresh["actions"] == ["*"] and isinstance(fresh["actions"], list)
    assert fresh["aud_target"] is None
    assert isinstance(fresh["caller"], str) and isinstance(fresh["svc"], str)
