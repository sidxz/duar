"""forward_headers(): call another first-party app as the same user, from verified state."""

import uuid

import pytest
from starlette.requests import Request

from duar_auth import DuarError, forward_headers
from duar_auth.types import AuthenticatedUser


def _request(state: dict, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers or [], "state": state})


def _user(workspace_id: uuid.UUID) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="a@b.c",
        name="A",
        workspace_id=workspace_id,
        workspace_slug="ws",
        workspace_role="editor",
    )


def test_authz_mode_forwards_idp_token_and_verified_workspace():
    w = uuid.uuid4()
    req = _request(
        {"user": _user(w), "token": "authz", "idp_token": "idp"},
        headers=[(b"x-workspace-id", b"other")],
    )
    assert forward_headers(req) == {"Authorization": "Bearer idp", "X-Workspace-Id": str(w)}


def test_without_idp_token_raises():
    req = _request({"user": _user(uuid.uuid4()), "token": "acc"})
    with pytest.raises(DuarError) as exc:
        forward_headers(req)
    assert exc.value.status_code == 401


def test_unauthenticated_raises():
    with pytest.raises(DuarError) as exc:
        forward_headers(_request({}))
    assert exc.value.status_code == 401
