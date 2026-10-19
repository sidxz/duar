"""mount_mcp(): FastMCP served inside a Duar-protected app, end to end.

Real FastMCP over HTTP + the real AuthzMiddleware with auto-resolve (the IdP token
plus X-Workspace-Id is minted into an authz token by a fake AuthzClient); only the
Duar role API is stubbed.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from starlette.testclient import TestClient
from test_authz_middleware import (
    AUTO_WS,
    TEST_IDP_AUDIENCE,
    TEST_SERVICE_NAME,
    _FakeAuthzClient,
    _jwks_for,
    _patch_jwks,
    _signed_dual,
)

from duar_auth import Duar, DuarError
from duar_auth.mcp import AGENT_GATED, DuarAuthMiddleware, mount_mcp


class _RoleStub:
    def __init__(self, allowed=True, register_error: Exception | None = None, events: list | None = None):
        self.allowed = allowed
        self.register_error = register_error
        self.events = events if events is not None else []
        self.service_name = TEST_SERVICE_NAME
        self.check_calls: list[tuple] = []
        self.register_calls: list[list[dict]] = []

    async def check_action(self, token, action, workspace_id):
        self.check_calls.append((token, action, workspace_id))
        return self.allowed

    async def register_actions(self, actions):
        self.events.append("sync")
        self.register_calls.append(actions)
        if self.register_error is not None:
            raise self.register_error


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, pub.decode()


@pytest.fixture(scope="module")
def keys():
    return _keypair(), _keypair()


@pytest.fixture
def idp_token(keys, monkeypatch):
    (idp_priv, _), (duar_priv, duar_pub) = keys
    _patch_jwks(monkeypatch, _jwks_for(duar_pub, "s1"))
    return _signed_dual(idp_priv, duar_priv, "s1")[0]


def _duar(keys, role: _RoleStub) -> Duar:
    (_, idp_pub), (duar_priv, _) = keys
    duar = Duar(
        "http://duar",
        TEST_SERVICE_NAME,
        "sk",
        idp_public_key=idp_pub,
        idp_audience=TEST_IDP_AUDIENCE,
        auto_resolve=True,
        idp_provider="google",
    )
    duar._authz = _FakeAuthzClient(duar_priv)
    duar._roles = role
    return duar


def _mcp(duar: Duar) -> FastMCP:
    mcp = FastMCP("t")

    @mcp.tool
    def whoami_tool() -> str:
        """Ungated: the caller's workspace."""
        return str(duar.request_auth(get_http_request()).workspace_id)

    @mcp.tool(tags={AGENT_GATED})
    def secret_tool() -> str:
        """Gated tool."""
        return "secret"

    return mcp


def _mounted(duar: Duar, lifespan=None) -> FastAPI:
    app = FastAPI(lifespan=lifespan) if lifespan else FastAPI()
    duar.protect(app)
    mount_mcp(app, _mcp(duar), duar)
    return app


def _rpc(client, name: str, headers: dict, path: str = "/mcp/", **kw):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
    return client.post(path, json=body, headers={"accept": "application/json, text/event-stream", **headers}, **kw)


def _result(resp) -> dict:
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :])["result"]
    raise AssertionError(f"no JSON-RPC result in {resp.status_code}: {resp.text[:200]}")


def _auth(idp_token: str) -> dict:
    return {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}


def test_tool_sees_authenticated_user(keys, idp_token):
    role = _RoleStub()
    with TestClient(_mounted(_duar(keys, role))) as client:
        result = _result(_rpc(client, "whoami_tool", _auth(idp_token)))
    assert result["structuredContent"]["result"] == AUTO_WS
    assert role.check_calls == []


def test_gated_tool_checks_mcp_action(keys, idp_token):
    role = _RoleStub(allowed=True)
    with TestClient(_mounted(_duar(keys, role))) as client:
        result = _result(_rpc(client, "secret_tool", _auth(idp_token)))
    assert result["isError"] is False
    token, action, workspace_id = role.check_calls[0]
    assert action == "mcp:team-notes.secret_tool"
    assert workspace_id == uuid.UUID(AUTO_WS)
    assert pyjwt.decode(token, options={"verify_signature": False})["svc"] == TEST_SERVICE_NAME


def test_gated_tool_denied_is_tool_error(keys, idp_token):
    with TestClient(_mounted(_duar(keys, _RoleStub(allowed=False)))) as client:
        result = _result(_rpc(client, "secret_tool", _auth(idp_token)))
    assert result["isError"] is True
    assert "Not permitted" in result["content"][0]["text"]


def test_missing_auth_is_401(keys):
    with TestClient(_mounted(_duar(keys, _RoleStub()))) as client:
        resp = _rpc(client, "whoami_tool", {})
    assert resp.status_code == 401


def test_startup_registers_gated_tools(keys):
    role = _RoleStub()
    with TestClient(_mounted(_duar(keys, role))):
        pass
    assert role.register_calls == [[{"action": "mcp:team-notes.secret_tool", "description": "Gated tool."}]]


def test_startup_sync_failure_does_not_block_app(keys, idp_token):
    role = _RoleStub(allowed=False, register_error=DuarError("down", 503))
    with TestClient(_mounted(_duar(keys, role))) as client:
        assert _result(_rpc(client, "whoami_tool", _auth(idp_token)))["isError"] is False
        assert _result(_rpc(client, "secret_tool", _auth(idp_token)))["isError"] is True


def test_mcp_without_trailing_slash_redirects_and_works(keys, idp_token):
    with TestClient(_mounted(_duar(keys, _RoleStub()))) as client:
        resp = _rpc(client, "whoami_tool", _auth(idp_token), path="/mcp", follow_redirects=True)
    assert _result(resp)["structuredContent"]["result"] == AUTO_WS


def test_mount_requires_protect(keys):
    duar = _duar(keys, _RoleStub())
    with pytest.raises(RuntimeError, match="duar.protect"):
        mount_mcp(FastAPI(), _mcp(duar), duar)


def test_host_lifespan_runs_before_sync(keys, idp_token):
    events: list[str] = []

    @asynccontextmanager
    async def host_lifespan(app):
        events.append("host-start")
        yield
        events.append("host-stop")

    role = _RoleStub(events=events)
    with TestClient(_mounted(_duar(keys, role), lifespan=host_lifespan)) as client:
        assert _result(_rpc(client, "whoami_tool", _auth(idp_token)))["isError"] is False
    assert events == ["host-start", "sync", "host-stop"]


def test_server_is_stateless(keys, idp_token):
    with TestClient(_mounted(_duar(keys, _RoleStub()))) as client:
        resp = _rpc(client, "whoami_tool", _auth(idp_token))
    assert resp.status_code == 200
    assert "mcp-session-id" not in resp.headers


def test_standalone_http_app_with_duar_middleware(keys, idp_token):
    duar = _duar(keys, _RoleStub())
    mcp = _mcp(duar)
    mcp.add_middleware(DuarAuthMiddleware(duar))
    app = mcp.http_app(path="/mcp", stateless_http=True, middleware=duar.middleware())
    with TestClient(app) as client:
        result = _result(_rpc(client, "whoami_tool", _auth(idp_token), path="/mcp"))
    assert result["structuredContent"]["result"] == AUTO_WS
