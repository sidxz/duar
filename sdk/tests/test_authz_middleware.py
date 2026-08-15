"""Tests for dual-token AuthZ middleware."""

import datetime
import uuid

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
            "aud": "sentinel:authz",
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
                "aud": "sentinel:authz",
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
                "aud": "sentinel:authz",
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
            "aud": "sentinel:authz",
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


class _FakeHTTPResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        import io

        return io.BytesIO(self._payload)

    def __exit__(self, *exc):
        return False


def _patch_jwks(monkeypatch, jwks: dict) -> None:
    """Make PyJWKClient (urllib-based) serve this JWKS on every fetch."""
    import json
    import urllib.request

    payload = json.dumps(jwks).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeHTTPResponse(payload))


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
            "aud": "sentinel:authz",
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
