"""Duar.middleware() (what protect() installs, as a list) and Duar.request_auth()."""

import uuid

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from duar_auth import AuthzMiddleware, Duar, DuarError, JWTAuthMiddleware
from duar_auth.types import AuthenticatedUser


def _authz_duar(public_pem: str) -> Duar:
    return Duar(
        base_url="https://duar.test",
        service_name="reports",
        service_key="svc-key",
        idp_public_key=public_pem,
        idp_audience="aud",
        auto_resolve=True,
        idp_provider="google",
    )


def _request(**state) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "state": dict(state)})


def test_middleware_authz_mode(rsa_keypair):
    duar = _authz_duar(rsa_keypair[1])
    mws = duar.middleware(["/health"])
    assert len(mws) == 1
    assert mws[0].cls is AuthzMiddleware
    assert mws[0].kwargs["auto_resolve"] is True
    assert mws[0].kwargs["duar_instance"] is duar
    assert mws[0].kwargs["exclude_paths"] == ["/health"]


def test_middleware_proxy_mode():
    duar = Duar(base_url="https://duar.test", service_name="reports", service_key="svc-key", mode="proxy")
    mws = duar.middleware()
    assert len(mws) == 1
    assert mws[0].cls is JWTAuthMiddleware
    assert mws[0].kwargs["base_url"] == duar.base_url


def test_protect_installs_same_middleware(rsa_keypair):
    duar = _authz_duar(rsa_keypair[1])
    app = FastAPI()
    duar.protect(app, ["/health"])
    assert app.user_middleware[0].cls is AuthzMiddleware
    assert app.user_middleware[0].kwargs == duar.middleware(["/health"])[0].kwargs


def test_request_auth_from_state(rsa_keypair):
    duar = _authz_duar(rsa_keypair[1])
    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="a@b.c",
        name="A",
        workspace_id=uuid.uuid4(),
        workspace_slug="ws",
        workspace_role="editor",
    )
    auth = duar.request_auth(_request(user=user, token="tok"))
    assert auth.user is user
    assert auth._token == "tok"
    assert auth._roles is duar.roles
    assert auth._permissions is duar.permissions


def test_request_auth_unauthenticated(rsa_keypair):
    duar = _authz_duar(rsa_keypair[1])
    with pytest.raises(DuarError) as exc:
        duar.request_auth(_request())
    assert exc.value.status_code == 401
