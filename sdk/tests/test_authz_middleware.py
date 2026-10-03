"""Tests for dual-token AuthZ middleware."""

import asyncio
import datetime
import uuid

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from duar_auth.authz_middleware import AuthzMiddleware
from duar_auth.types import DuarError


@pytest.fixture(scope="module")
def idp_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


@pytest.fixture(scope="module")
def duar_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


TEST_IDP_AUDIENCE = "my-oauth-client.apps.googleusercontent.com"
TEST_SERVICE_NAME = "team-notes"


@pytest.fixture()
def dual_tokens(idp_keypair, duar_keypair):
    idp_priv, _ = idp_keypair
    duar_priv, _ = duar_keypair
    now = datetime.datetime.now(datetime.UTC)
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    idp_sub = "google|12345"

    idp_token = pyjwt.encode(
        {
            "sub": idp_sub,
            "aud": TEST_IDP_AUDIENCE,
            "email": "alice@acme.com",
            "name": "Alice",
            "iat": now,
            "exp": now + datetime.timedelta(hours=1),
        },
        idp_priv,
        algorithm="RS256",
    )
    authz_token = pyjwt.encode(
        {
            "sub": str(user_id),
            "idp_sub": idp_sub,
            "svc": TEST_SERVICE_NAME,
            "wid": str(workspace_id),
            "wslug": "acme",
            "wrole": "editor",
            "actions": ["read"],
            "aud": "duar:authz",
            "iat": now,
            "exp": now + datetime.timedelta(minutes=5),
        },
        duar_priv,
        algorithm="RS256",
    )
    return idp_token, authz_token


def _make_app(idp_pub_key: str, duar_pub_key: str) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        user = request.state.user
        return JSONResponse({"email": user.email, "role": user.workspace_role})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(
        AuthzMiddleware,
        service_name=TEST_SERVICE_NAME,
        idp_audience=TEST_IDP_AUDIENCE,
        idp_public_key=idp_pub_key,
        duar_public_key=duar_pub_key,
    )
    return app


class TestAuthzMiddleware:
    def test_valid_dual_tokens(self, idp_keypair, duar_keypair, dual_tokens):
        _, idp_pub = idp_keypair
        _, duar_pub = duar_keypair
        idp_token, authz_token = dual_tokens
        client = TestClient(_make_app(idp_pub, duar_pub))
        resp = client.get(
            "/protected",
            headers={
                "Authorization": f"Bearer {idp_token}",
                "X-Authz-Token": authz_token,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "alice@acme.com"
        assert resp.json()["role"] == "editor"

    def test_missing_authz_token(self, idp_keypair, duar_keypair, dual_tokens):
        _, idp_pub = idp_keypair
        _, duar_pub = duar_keypair
        idp_token, _ = dual_tokens
        client = TestClient(_make_app(idp_pub, duar_pub))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {idp_token}"})
        assert resp.status_code == 401

    def test_mismatched_idp_sub_rejected(self, idp_keypair, duar_keypair):
        idp_priv, idp_pub = idp_keypair
        duar_priv, duar_pub = duar_keypair
        now = datetime.datetime.now(datetime.UTC)

        idp_token = pyjwt.encode(
            {
                "sub": "google|ATTACKER",
                "aud": TEST_IDP_AUDIENCE,
                "email": "evil@evil.com",
                "iat": now,
                "exp": now + datetime.timedelta(hours=1),
            },
            idp_priv,
            algorithm="RS256",
        )
        authz_token = pyjwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "idp_sub": "google|VICTIM",
                "svc": TEST_SERVICE_NAME,
                "wid": str(uuid.uuid4()),
                "wslug": "acme",
                "wrole": "owner",
                "actions": [],
                "aud": "duar:authz",
                "iat": now,
                "exp": now + datetime.timedelta(minutes=5),
            },
            duar_priv,
            algorithm="RS256",
        )
        client = TestClient(_make_app(idp_pub, duar_pub))
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {idp_token}", "X-Authz-Token": authz_token},
        )
        assert resp.status_code == 401
        assert "binding" in resp.json()["detail"].lower()

    def test_wrong_audience_rejected(self, idp_keypair, duar_keypair, dual_tokens):
        """An IdP token with the wrong aud must be rejected even if signature is valid."""
        idp_priv, idp_pub = idp_keypair
        _, duar_pub = duar_keypair
        _, authz_token = dual_tokens
        now = datetime.datetime.now(datetime.UTC)

        # Valid signature, valid sub, but audience = attacker's OAuth client
        bad_audience_token = pyjwt.encode(
            {
                "sub": "google|12345",
                "aud": "attacker-client-id.apps.googleusercontent.com",
                "email": "alice@acme.com",
                "iat": now,
                "exp": now + datetime.timedelta(hours=1),
            },
            idp_priv,
            algorithm="RS256",
        )
        client = TestClient(_make_app(idp_pub, duar_pub))
        resp = client.get(
            "/protected",
            headers={
                "Authorization": f"Bearer {bad_audience_token}",
                "X-Authz-Token": authz_token,
            },
        )
        assert resp.status_code == 401

    def test_wrong_svc_rejected(self, idp_keypair, duar_keypair):
        """An authz token with a different svc claim must be rejected."""
        idp_priv, idp_pub = idp_keypair
        duar_priv, duar_pub = duar_keypair
        now = datetime.datetime.now(datetime.UTC)
        idp_sub = "google|12345"

        idp_token = pyjwt.encode(
            {
                "sub": idp_sub,
                "aud": TEST_IDP_AUDIENCE,
                "email": "alice@acme.com",
                "iat": now,
                "exp": now + datetime.timedelta(hours=1),
            },
            idp_priv,
            algorithm="RS256",
        )
        # Token minted for another service
        authz_token = pyjwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "idp_sub": idp_sub,
                "svc": "other-service",
                "wid": str(uuid.uuid4()),
                "wslug": "acme",
                "wrole": "owner",
                "actions": [],
                "aud": "duar:authz",
                "iat": now,
                "exp": now + datetime.timedelta(minutes=5),
            },
            duar_priv,
            algorithm="RS256",
        )
        client = TestClient(_make_app(idp_pub, duar_pub))
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {idp_token}", "X-Authz-Token": authz_token},
        )
        assert resp.status_code == 403
        assert "different service" in resp.json()["detail"].lower()


class TestAuthzMiddlewareOrgClaims:
    """Org claims (oid/oslug/opub) are parsed from the authz-token payload."""

    def test_org_claims_parsed(self, idp_keypair, duar_keypair):
        idp_priv, idp_pub = idp_keypair
        duar_priv, duar_pub = duar_keypair
        now = datetime.datetime.now(datetime.UTC)
        idp_sub = "google|12345"
        org_id = uuid.uuid4()

        idp_token = pyjwt.encode(
            {
                "sub": idp_sub,
                "aud": TEST_IDP_AUDIENCE,
                "email": "alice@acme.com",
                "name": "Alice",
                "iat": now,
                "exp": now + datetime.timedelta(hours=1),
            },
            idp_priv,
            algorithm="RS256",
        )
        authz_payload = {
            "sub": str(uuid.uuid4()),
            "idp_sub": idp_sub,
            "svc": TEST_SERVICE_NAME,
            "wid": str(uuid.uuid4()),
            "wslug": "acme",
            "wrole": "editor",
            "actions": ["read"],
            "aud": "duar:authz",
            "iat": now,
            "exp": now + datetime.timedelta(minutes=5),
        }
        authz_payload["oid"] = str(org_id)
        authz_payload["oslug"] = "abbvie"
        authz_payload["opub"] = True
        authz_token = pyjwt.encode(authz_payload, duar_priv, algorithm="RS256")

        captured_user = None

        async def protected(request: Request) -> JSONResponse:
            nonlocal captured_user
            captured_user = request.state.user
            return JSONResponse({"email": captured_user.email})

        app = Starlette(routes=[Route("/protected", protected)])
        app.add_middleware(
            AuthzMiddleware,
            service_name=TEST_SERVICE_NAME,
            idp_audience=TEST_IDP_AUDIENCE,
            idp_public_key=idp_pub,
            duar_public_key=duar_pub,
        )
        client = TestClient(app)
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {idp_token}", "X-Authz-Token": authz_token},
        )

        assert resp.status_code == 200
        assert captured_user.org_id == org_id
        assert captured_user.org_slug == "abbvie"
        assert captured_user.org_is_public is True

    def test_missing_org_claims_default_none(self, idp_keypair, duar_keypair, dual_tokens):
        _, idp_pub = idp_keypair
        _, duar_pub = duar_keypair
        idp_token, authz_token = dual_tokens

        captured_user = None

        async def protected(request: Request) -> JSONResponse:
            nonlocal captured_user
            captured_user = request.state.user
            return JSONResponse({"email": captured_user.email})

        app = Starlette(routes=[Route("/protected", protected)])
        app.add_middleware(
            AuthzMiddleware,
            service_name=TEST_SERVICE_NAME,
            idp_audience=TEST_IDP_AUDIENCE,
            idp_public_key=idp_pub,
            duar_public_key=duar_pub,
        )
        client = TestClient(app)
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {idp_token}", "X-Authz-Token": authz_token},
        )

        assert resp.status_code == 200
        assert captured_user.org_id is None
        assert captured_user.org_slug is None
        assert captured_user.org_is_public is False


class _FakeDuar:
    """Minimal stand-in exposing what AuthzMiddleware reads from a Duar."""

    def __init__(self, base_url="http://duar"):
        self.base_url = base_url
        self.idp_public_key = None
        self.idp_jwks_url = None
        self.duar_public_key = None


def _jwks_for(public_pem: str, kid: str) -> dict:
    import json

    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from jwt.algorithms import RSAAlgorithm

    jwk = json.loads(RSAAlgorithm.to_jwk(load_pem_public_key(public_pem.encode())))
    jwk.update({"use": "sig", "alg": "RS256", "kid": kid})
    return {"keys": [jwk]}


def _patch_jwks(monkeypatch, jwks: dict) -> None:
    """Make PyJWKClient serve this JWKS on every fetch. Patches fetch_data (PyJWT's
    documented override point), not the transport, which PyJWT 2.14 changed."""
    from jwt import PyJWKClient

    monkeypatch.setattr(PyJWKClient, "fetch_data", lambda self: jwks)


def _signed_dual(idp_priv, duar_priv, kid):
    now = datetime.datetime.now(datetime.UTC)
    idp_sub = "google|12345"
    idp_token = pyjwt.encode(
        {
            "sub": idp_sub,
            "aud": TEST_IDP_AUDIENCE,
            "email": "alice@acme.com",
            "name": "Alice",
            "iat": now,
            "exp": now + datetime.timedelta(hours=1),
        },
        idp_priv,
        algorithm="RS256",
    )
    authz_token = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "idp_sub": idp_sub,
            "svc": TEST_SERVICE_NAME,
            "wid": str(uuid.uuid4()),
            "wslug": "acme",
            "wrole": "editor",
            "actions": ["read"],
            "aud": "duar:authz",
            "iat": now,
            "exp": now + datetime.timedelta(minutes=5),
        },
        duar_priv,
        algorithm="RS256",
        headers={"kid": kid},
    )
    return idp_token, authz_token


def _make_instance_app(idp_pub, fake_duar) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        return JSONResponse({"email": request.state.user.email})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(
        AuthzMiddleware,
        service_name=TEST_SERVICE_NAME,
        idp_audience=TEST_IDP_AUDIENCE,
        idp_public_key=idp_pub,
        duar_instance=fake_duar,
    )
    return app


class TestAuthzKidPath:
    """The authz-token key is resolved by kid via PyJWKClient against Duar's
    JWKS; we verify the delegation and the unknown-kid error mapping."""

    def test_authz_token_resolved_by_kid_via_jwks(self, idp_keypair, duar_keypair, monkeypatch):
        idp_priv, idp_pub = idp_keypair
        duar_priv, duar_pub = duar_keypair
        _patch_jwks(monkeypatch, _jwks_for(duar_pub, "s1"))
        idp_token, authz_token = _signed_dual(idp_priv, duar_priv, "s1")
        client = TestClient(_make_instance_app(idp_pub, _FakeDuar()))
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {idp_token}", "X-Authz-Token": authz_token},
        )
        assert resp.status_code == 200

    def test_unknown_authz_kid_rejected_401(self, idp_keypair, duar_keypair, monkeypatch):
        idp_priv, idp_pub = idp_keypair
        duar_priv, duar_pub = duar_keypair
        _patch_jwks(monkeypatch, _jwks_for(duar_pub, "s1"))
        # authz token's kid is not published → PyJWKClient refetches, misses, raises.
        idp_token, authz_token = _signed_dual(idp_priv, duar_priv, "other")
        client = TestClient(_make_instance_app(idp_pub, _FakeDuar()))
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {idp_token}", "X-Authz-Token": authz_token},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# auto_resolve: no X-Authz-Token + X-Workspace-Id → middleware mints via Duar
# ---------------------------------------------------------------------------

AUTO_WS = str(uuid.uuid4())


def _mint_authz(duar_priv, workspace_id: str, kid: str = "s1") -> str:
    """What Duar's /authz/resolve would sign for this workspace."""
    now = datetime.datetime.now(datetime.UTC)
    return pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "idp_sub": "google|12345",
            "svc": TEST_SERVICE_NAME,
            "wid": str(workspace_id),
            "wslug": "acme",
            "wrole": "editor",
            "actions": ["read"],
            "aud": "duar:authz",
            "iat": now,
            "exp": now + datetime.timedelta(minutes=5),
        },
        duar_priv,
        algorithm="RS256",
        headers={"kid": kid},
    )


class _FakeAuthzClient:
    """Stand-in for AuthzClient: records resolve() calls, returns a signed mint or raises."""

    def __init__(self, duar_priv, *, fail: Exception | None = None, delay: float = 0.0, response: dict | None = None):
        self.calls: list[tuple[str, str, str]] = []
        self._duar_priv = duar_priv
        self._fail = fail
        self._delay = delay
        self._response = response

    async def resolve(self, idp_token, provider, workspace_id=None, nonce=None):
        self.calls.append((idp_token, provider, str(workspace_id)))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail is not None:
            raise self._fail
        if self._response is not None:
            return self._response
        return {"authz_token": _mint_authz(self._duar_priv, str(workspace_id)), "expires_in": 300}


class _FakeAutoDuar(_FakeDuar):
    """_FakeDuar plus the ``authz`` client the auto path mints through."""

    def __init__(self, authz):
        super().__init__()
        self.authz = authz


def _make_auto_app(idp_pub, fake_duar, *, auto_resolve: bool = True) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        return JSONResponse({"email": request.state.user.email, "token": request.state.token})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(
        AuthzMiddleware,
        service_name=TEST_SERVICE_NAME,
        idp_audience=TEST_IDP_AUDIENCE,
        idp_public_key=idp_pub,
        duar_instance=fake_duar,
        idp_provider="google",
        auto_resolve=auto_resolve,
    )
    return app


@pytest.fixture()
def auto_env(idp_keypair, duar_keypair, monkeypatch):
    """JWKS served for kid s1, a verified IdP token, and a recording fake authz client."""
    idp_priv, idp_pub = idp_keypair
    duar_priv, duar_pub = duar_keypair
    _patch_jwks(monkeypatch, _jwks_for(duar_pub, "s1"))
    idp_token, authz_token = _signed_dual(idp_priv, duar_priv, "s1")
    return idp_pub, duar_priv, idp_token, authz_token


class TestAutoResolve:
    def test_missing_both_tokens_401_with_hint(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {idp_token}"})
        assert resp.status_code == 401
        assert "X-Workspace-Id" in resp.json()["detail"]
        assert authz.calls == []

    def test_workspace_header_mints_and_sets_state(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "alice@acme.com"
        assert authz.calls == [(idp_token, "google", AUTO_WS)]
        minted = pyjwt.decode(resp.json()["token"], options={"verify_signature": False})
        assert minted["wid"] == AUTO_WS

    def test_second_request_hits_cache(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        headers = {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        assert client.get("/protected", headers=headers).status_code == 200
        assert client.get("/protected", headers=headers).status_code == 200
        assert len(authz.calls) == 1

    def test_cache_key_includes_workspace(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        other_ws = str(uuid.uuid4())
        for ws in (AUTO_WS, other_ws):
            resp = client.get("/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": ws})
            assert resp.status_code == 200
        assert [c[2] for c in authz.calls] == [AUTO_WS, other_ws]

    async def test_concurrent_misses_share_one_mint(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, delay=0.05)
        app = _make_auto_app(idp_pub, _FakeAutoDuar(authz))
        headers = {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            resps = await asyncio.gather(*[c.get("/protected", headers=headers) for _ in range(5)])
        assert [r.status_code for r in resps] == [200] * 5
        assert len(authz.calls) == 1

    def test_authz_header_wins_over_workspace_header(self, auto_env):
        idp_pub, duar_priv, idp_token, authz_token = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get(
            "/protected",
            headers={
                "Authorization": f"Bearer {idp_token}",
                "X-Authz-Token": authz_token,
                "X-Workspace-Id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 200
        assert authz.calls == []

    def test_bad_workspace_uuid_400(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get(
            "/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": "not-a-uuid"}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid X-Workspace-Id"
        assert authz.calls == []

    def test_invalid_idp_token_never_reaches_duar(self, auto_env):
        idp_pub, duar_priv, _, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get("/protected", headers={"Authorization": "Bearer junk", "X-Workspace-Id": AUTO_WS})
        assert resp.status_code == 401
        assert authz.calls == []

    def test_auto_resolve_off_keeps_existing_401(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz), auto_resolve=False))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Missing authz token"
        assert authz.calls == []

    def test_requires_duar_instance(self, idp_keypair, duar_keypair):
        _, idp_pub = idp_keypair
        _, duar_pub = duar_keypair
        with pytest.raises(ValueError, match="duar_instance"):
            AuthzMiddleware(
                Starlette(),
                service_name=TEST_SERVICE_NAME,
                idp_audience=TEST_IDP_AUDIENCE,
                idp_public_key=idp_pub,
                duar_public_key=duar_pub,
                idp_provider="google",
                auto_resolve=True,
            )

    def test_requires_idp_provider(self, idp_keypair):
        _, idp_pub = idp_keypair
        with pytest.raises(ValueError, match="idp_provider"):
            AuthzMiddleware(
                Starlette(),
                service_name=TEST_SERVICE_NAME,
                idp_audience=TEST_IDP_AUDIENCE,
                idp_public_key=idp_pub,
                duar_instance=_FakeDuar(),
                auto_resolve=True,
            )

    @pytest.mark.parametrize(
        ("duar_status", "expected_status", "expected_detail"),
        [
            (400, 401, "IdP token rejected by Duar"),
            (401, 503, "Authorization service rejected the service key"),
            (403, 403, "Not authorized for this workspace"),
            (409, 403, "Not authorized for this workspace"),
            (500, 503, "Authorization service unavailable"),
        ],
    )
    def test_duar_error_mapping(self, auto_env, duar_status, expected_status, expected_detail):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, fail=DuarError("Duar API error", duar_status))
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS})
        assert resp.status_code == expected_status
        assert resp.json()["detail"] == expected_detail

    def test_rate_limited_passes_retry_after(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, fail=DuarError("Duar API error", 429, retry_after="17"))
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS})
        assert resp.status_code == 429
        assert resp.json()["detail"] == "Authorization service rate limit"
        assert resp.headers["Retry-After"] == "17"

    def test_network_error_503(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, fail=httpx.ConnectError("boom"))
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS})
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Authorization service unavailable"

    def test_failed_mint_is_not_cached(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, fail=DuarError("Duar API error", 500))
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        headers = {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        assert client.get("/protected", headers=headers).status_code == 503
        authz._fail = None  # Duar recovers
        assert client.get("/protected", headers=headers).status_code == 200
        assert len(authz.calls) == 2

    def test_duar_400_detail_is_surfaced(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        err = DuarError("Duar API error", 400, detail="Unsupported provider: entra")
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(_FakeAuthzClient(duar_priv, fail=err))))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "IdP token rejected by Duar: Unsupported provider: entra"

    @pytest.mark.parametrize(
        "authz",
        [
            pytest.param(lambda k: _FakeAuthzClient(k, fail=ValueError("Expecting value")), id="non-json"),
            pytest.param(lambda k: _FakeAuthzClient(k, response={"workspaces": []}), id="no-token"),
            pytest.param(lambda k: _FakeAuthzClient(k, response={"authz_token": ""}), id="empty-token"),
        ],
    )
    def test_unusable_mint_response_503_not_cached(self, auto_env, authz):
        idp_pub, duar_priv, idp_token, _ = auto_env
        fake = authz(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(fake)))
        headers = {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        assert client.get("/protected", headers=headers).status_code == 503
        assert client.get("/protected", headers=headers).json()["detail"] == "Authorization service unavailable"
        assert len(fake.calls) == 2

    def test_unverifiable_minted_token_is_evicted(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        # Signed with a kid Duar's JWKS does not publish → local verification fails.
        bad = {"authz_token": _mint_authz(duar_priv, AUTO_WS, kid="rotated-away"), "expires_in": 300}
        fake = _FakeAuthzClient(duar_priv, response=bad)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(fake)))
        headers = {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        resp = client.get("/protected", headers=headers)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid authz token"
        client.get("/protected", headers=headers)
        assert len(fake.calls) == 2  # not served from cache

    def test_zero_expires_in_is_not_cached(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        fake = _FakeAuthzClient(duar_priv, response={"authz_token": _mint_authz(duar_priv, AUTO_WS), "expires_in": 0})
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(fake)))
        headers = {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        assert client.get("/protected", headers=headers).status_code == 200
        assert client.get("/protected", headers=headers).status_code == 200
        assert len(fake.calls) == 2

    def test_idp_token_without_sub_never_reaches_duar(self, auto_env, idp_keypair):
        idp_pub, duar_priv, _, _ = auto_env
        idp_priv, _ = idp_keypair
        now = datetime.datetime.now(datetime.UTC)
        subless = pyjwt.encode(
            {"aud": TEST_IDP_AUDIENCE, "email": "alice@acme.com", "iat": now, "exp": now + datetime.timedelta(hours=1)},
            idp_priv,
            algorithm="RS256",
        )
        fake = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(fake)))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {subless}", "X-Workspace-Id": AUTO_WS})
        assert resp.status_code == 401
        assert fake.calls == []
