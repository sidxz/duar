"""Duar.request_auth(): the RequestAuth for a request the Duar middleware authenticated."""

import uuid

import pytest
from starlette.requests import Request

from duar_auth import Duar, DuarError
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
