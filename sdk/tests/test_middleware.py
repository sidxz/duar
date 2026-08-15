"""Tests for JWTAuthMiddleware."""

import datetime
import uuid

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from duar_auth.middleware import JWTAuthMiddleware


def _make_app(public_key: str) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        user = request.state.user
        return JSONResponse({"email": user.email, "role": user.workspace_role})

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/protected", protected), Route("/health", health)])
    app.add_middleware(JWTAuthMiddleware, public_key=public_key)
    return app


class TestJWTMiddleware:
    def test_valid_token(self, rsa_keypair, valid_token):
        _, pub = rsa_keypair
        client = TestClient(_make_app(pub))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {valid_token}"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "alice@example.com"

    def test_missing_auth_header(self, rsa_keypair):
        _, pub = rsa_keypair
        client = TestClient(_make_app(pub))
        resp = client.get("/protected")
        assert resp.status_code == 401
        assert "Missing" in resp.json()["detail"]

    def test_expired_token(self, rsa_keypair, jwt_payload, make_token):
        _, pub = rsa_keypair
        jwt_payload["exp"] = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
        token = make_token(jwt_payload)
        client = TestClient(_make_app(pub))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"]

    def test_invalid_signature(self, rsa_keypair, valid_token):
        # Use a different key to verify — should fail
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

        other_key = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
        other_pub = (
            other_key.public_key()
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            .decode()
        )
        client = TestClient(_make_app(other_pub))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {valid_token}"})
        assert resp.status_code == 401
        assert "Invalid token" in resp.json()["detail"]

    def test_excluded_path_skips_auth(self, rsa_keypair):
        _, pub = rsa_keypair
        client = TestClient(_make_app(pub))
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_malformed_bearer(self, rsa_keypair):
        _, pub = rsa_keypair
        client = TestClient(_make_app(pub))
        resp = client.get("/protected", headers={"Authorization": "Basic abc"})
        assert resp.status_code == 401

    def test_allowed_workspaces_permits_matching(self, rsa_keypair, valid_token, workspace_id):
        _, pub = rsa_keypair
        app = _make_app(pub)
        # Re-create with allowed_workspaces containing the token's workspace
        app = Starlette(routes=[Route("/protected", _make_app(pub).routes[0].endpoint)])
        app.add_middleware(JWTAuthMiddleware, public_key=pub, allowed_workspaces={str(workspace_id)})
        client = TestClient(app)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {valid_token}"})
        assert resp.status_code == 200

    def test_allowed_workspaces_rejects_non_matching(self, rsa_keypair, valid_token):
        _, pub = rsa_keypair
        app = Starlette(routes=[Route("/protected", _make_app(pub).routes[0].endpoint)])
        allowed = {"00000000-0000-0000-0000-000000000000"}
        app.add_middleware(JWTAuthMiddleware, public_key=pub, allowed_workspaces=allowed)
        client = TestClient(app)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {valid_token}"})
        assert resp.status_code == 403
        assert "Workspace not permitted" in resp.json()["detail"]

    def test_allowed_workspaces_none_allows_all(self, rsa_keypair, valid_token):
        _, pub = rsa_keypair
        # Default behavior (None) — should allow any workspace
        client = TestClient(_make_app(pub))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {valid_token}"})
        assert resp.status_code == 200

    def test_middleware_sets_token_on_state(self, rsa_keypair, valid_token):
        """After successful auth, request.state.token should contain the raw JWT."""
        _, pub = rsa_keypair

        async def check_token(request: Request) -> JSONResponse:
            return JSONResponse({"has_token": hasattr(request.state, "token"), "token": request.state.token})

        app = Starlette(routes=[Route("/check", check_token)])
        app.add_middleware(JWTAuthMiddleware, public_key=pub)
        client = TestClient(app)
        resp = client.get("/check", headers={"Authorization": f"Bearer {valid_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_token"] is True
        assert data["token"] == valid_token

    def test_org_claims_parsed(self, rsa_keypair, jwt_payload, make_token):
        org_id = uuid.uuid4()
        jwt_payload["oid"] = str(org_id)
        jwt_payload["oslug"] = "abbvie"
        jwt_payload["opub"] = False
        _, pub = rsa_keypair
        token = make_token(jwt_payload)

        captured_user = None

        async def protected(request: Request) -> JSONResponse:
            nonlocal captured_user
            captured_user = request.state.user
            return JSONResponse({"email": captured_user.email})

        app = Starlette(routes=[Route("/protected", protected)])
        app.add_middleware(JWTAuthMiddleware, public_key=pub)
        client = TestClient(app)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200
        assert captured_user.org_id == org_id
        assert captured_user.org_slug == "abbvie"
        assert captured_user.org_is_public is False

    def test_missing_org_claims_default_none(self, rsa_keypair, valid_token):
        _, pub = rsa_keypair

        captured_user = None

        async def protected(request: Request) -> JSONResponse:
            nonlocal captured_user
            captured_user = request.state.user
            return JSONResponse({"email": captured_user.email})

        app = Starlette(routes=[Route("/protected", protected)])
        app.add_middleware(JWTAuthMiddleware, public_key=pub)
        client = TestClient(app)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {valid_token}"})

        assert resp.status_code == 200
        assert captured_user.org_id is None
        assert captured_user.org_slug is None
        assert captured_user.org_is_public is False


def _make_jwks_app(base_url: str) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        return JSONResponse({"email": request.state.user.email})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(JWTAuthMiddleware, base_url=base_url)
    return app


def _jwks_for(public_pem: str, kid: str) -> dict:
    import json

    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from jwt.algorithms import RSAAlgorithm

    jwk = json.loads(RSAAlgorithm.to_jwk(load_pem_public_key(public_pem.encode())))
    jwk.update({"use": "sig", "alg": "RS256", "kid": kid})
    return {"keys": [jwk]}


class _FakeHTTPResponse:
    """Context-manager stand-in for urllib.request.urlopen()'s return value."""

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


class TestJWKSPath:
    """The JWKS branch now delegates to PyJWKClient; we verify the delegation and
    the error mapping. PyJWKClient owns kid-selection + refetch-on-rotation."""

    def test_validates_token_whose_kid_is_published(self, rsa_keypair, jwt_payload, monkeypatch):
        import jwt as pyjwt

        priv, pub = rsa_keypair
        _patch_jwks(monkeypatch, _jwks_for(pub, "k1"))
        token = pyjwt.encode(jwt_payload, priv, algorithm="RS256", headers={"kid": "k1"})
        client = TestClient(_make_jwks_app("http://duar"))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_unknown_kid_is_rejected_401(self, rsa_keypair, jwt_payload, monkeypatch):
        import jwt as pyjwt

        priv, pub = rsa_keypair
        _patch_jwks(monkeypatch, _jwks_for(pub, "k1"))
        # Token's kid is not published → PyJWKClient refetches, still misses, raises.
        token = pyjwt.encode(jwt_payload, priv, algorithm="RS256", headers={"kid": "other"})
        client = TestClient(_make_jwks_app("http://duar"))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
