"""AuthzMiddleware accepts a realm-shared authz token (svc == effective_scope)."""

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
def keypairs():
    def _pair():
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = (
            k.public_key()
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            .decode()
        )
        return k, pub

    return {"idp": _pair(), "duar": _pair()}


class _FakeInstance:
    """Minimal stand-in for a Duar instance: only what the middleware reads."""

    idp_jwks_url = None

    def __init__(self, effective_scope: str):
        self.effective_scope = effective_scope


def _tokens(keypairs, *, svc: str):
    idp_priv, _ = keypairs["idp"]
    duar_priv, _ = keypairs["duar"]
    now = datetime.datetime.now(datetime.UTC)
    idp_sub = "google|123"
    idp_token = pyjwt.encode(
        {
            "sub": idp_sub,
            "aud": "client-id",
            "email": "a@b.com",
            "name": "A",
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
            "svc": svc,
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
    )
    return idp_token, authz_token


def _app(keypairs, *, effective_scope: str) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        return JSONResponse({"role": request.state.user.workspace_role})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(
        AuthzMiddleware,
        service_name="docs",  # the member's own name — NOT the realm slug
        idp_audience="client-id",
        idp_public_key=keypairs["idp"][1],
        duar_public_key=keypairs["duar"][1],
        duar_instance=_FakeInstance(effective_scope),
    )
    return app


def test_member_accepts_token_minted_for_realm_slug(keypairs):
    idp, authz = _tokens(keypairs, svc="acme-suite")  # realm-scoped token
    client = TestClient(_app(keypairs, effective_scope="acme-suite"))
    resp = client.get("/protected", headers={"Authorization": f"Bearer {idp}", "X-Authz-Token": authz})
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"


def test_member_rejects_token_minted_for_bare_service_name(keypairs):
    # A token whose svc is the old bare name must NOT validate once the service
    # is a realm member (effective_scope is the slug).
    idp, authz = _tokens(keypairs, svc="docs")
    client = TestClient(_app(keypairs, effective_scope="acme-suite"))
    resp = client.get("/protected", headers={"Authorization": f"Bearer {idp}", "X-Authz-Token": authz})
    assert resp.status_code == 403


def test_standalone_unchanged_accepts_service_name(keypairs):
    # effective_scope == service_name (standalone): today's behavior, unchanged.
    idp, authz = _tokens(keypairs, svc="docs")
    client = TestClient(_app(keypairs, effective_scope="docs"))
    resp = client.get("/protected", headers={"Authorization": f"Bearer {idp}", "X-Authz-Token": authz})
    assert resp.status_code == 200
