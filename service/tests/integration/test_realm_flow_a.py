# service/tests/integration/test_realm_flow_a.py
"""Flow A end-to-end: a REAL service-minted authz token (svc = realm slug) accepted by
the REAL Python AuthzMiddleware when the receiver's effective_scope is that slug, and
rejected when the token was minted for a different scope.

The authz token comes from the real create_authz_token minter (signed by the ambient
key_provider). The IdP token is minted with an in-test keypair — AuthzMiddleware is a
dual-token gate (IdP identity + Duar authz), so both are required.
"""

import datetime
import uuid

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from duar_auth.authz_middleware import AuthzMiddleware
from src.auth import key_provider
from src.auth.jwt import create_authz_token

_IDP_SUB = "google|1"
_IDP_AUD = "client-id"


def _idp_keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = (
        k.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    priv = k.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return priv, pub


def _idp_token(idp_priv: str) -> str:
    now = datetime.datetime.now(datetime.UTC)
    return pyjwt.encode(
        {
            "sub": _IDP_SUB,
            "aud": _IDP_AUD,
            "email": "a@b.com",
            "name": "A",
            "iat": now,
            "exp": now + datetime.timedelta(hours=1),
        },
        idp_priv,
        algorithm="RS256",
    )


def _real_authz_token(*, service_name: str) -> str:
    """A REAL Duar authz token, svc=service_name, bound to the IdP identity."""
    return create_authz_token(
        user_id=uuid.UUID(int=1),
        idp_sub=_IDP_SUB,
        workspace_id=uuid.UUID(int=2),
        workspace_slug="acme",
        workspace_role="editor",
        actions=["read"],
        service_name=service_name,
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )


def _duar_pubpem(token: str) -> str:
    kid = pyjwt.get_unverified_header(token)["kid"]
    return key_provider.verification_keys()[kid]


class _FakeInstance:
    """Minimal Duar stand-in: supplies effective_scope; no JWKS (static-key mode)."""

    idp_jwks_url = None

    def __init__(self, effective_scope: str):
        self.effective_scope = effective_scope


def _app(*, effective_scope: str, idp_pub: str, duar_pub: str) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        return JSONResponse({"role": request.state.user.workspace_role})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(
        AuthzMiddleware,
        service_name="app-b",  # the member's own name — NOT the realm slug
        idp_audience=_IDP_AUD,
        idp_public_key=idp_pub,
        duar_public_key=duar_pub,
        duar_instance=_FakeInstance(effective_scope),
    )
    return app


def test_flow_a_realm_member_accepts_realm_scoped_token():
    idp_priv, idp_pub = _idp_keypair()
    authz = _real_authz_token(service_name="acme-suite")  # minted for the realm slug
    client = TestClient(
        _app(
            effective_scope="acme-suite",
            idp_pub=idp_pub,
            duar_pub=_duar_pubpem(authz),
        )
    )
    resp = client.get(
        "/protected",
        headers={
            "Authorization": f"Bearer {_idp_token(idp_priv)}",
            "X-Authz-Token": authz,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"


def test_flow_a_member_rejects_token_minted_for_other_scope():
    idp_priv, idp_pub = _idp_keypair()
    authz = _real_authz_token(service_name="other-realm")  # wrong scope
    client = TestClient(
        _app(
            effective_scope="acme-suite",
            idp_pub=idp_pub,
            duar_pub=_duar_pubpem(authz),
        )
    )
    resp = client.get(
        "/protected",
        headers={
            "Authorization": f"Bearer {_idp_token(idp_priv)}",
            "X-Authz-Token": authz,
        },
    )
    assert resp.status_code == 403
