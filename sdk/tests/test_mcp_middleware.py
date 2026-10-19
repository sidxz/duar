"""DuarAuthMiddleware / sync_tools / agent_action — the Duar MCP gate.

Unit-tested with fakes for the FastMCP context, the HTTP request and the Duar role
client; no live server (the mounted, end-to-end path is in test_mcp_mount.py).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from duar_auth import mcp as mcp_mod
from duar_auth.mcp import AGENT_GATED, DuarAuthMiddleware, agent_action, sync_tools
from duar_auth.types import AuthenticatedUser

WID = uuid.UUID("11111111-1111-4111-8111-111111111111")


# ── fakes ──────────────────────────────────────────────────────────────


class _Server:
    def __init__(self, tools: dict[str, set[str]]):
        self._tools = tools

    async def get_tool(self, name):
        if name not in self._tools:
            raise KeyError(name)
        return SimpleNamespace(name=name, tags=self._tools[name])


class _Ctx:
    def __init__(self, name: str, server: _Server | None):
        self.message = SimpleNamespace(name=name, arguments={})
        self.fastmcp_context = SimpleNamespace(fastmcp=server) if server is not None else None


class _RoleStub:
    def __init__(self, allowed=True, raises=False, service_name="team-notes"):
        self.allowed = allowed
        self.raises = raises
        self.service_name = service_name
        self.calls: list[tuple] = []
        self.registered: list[list[dict]] = []

    async def check_action(self, token, action, workspace_id):
        self.calls.append((token, action, workspace_id))
        if self.raises:
            raise RuntimeError("duar down")
        return self.allowed

    async def register_actions(self, actions):
        self.registered.append(actions)


class _CallNext:
    def __init__(self):
        self.called = 0

    async def __call__(self, context):
        self.called += 1
        return "TOOL_RESULT"


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(), email="a@b.c", name="A", workspace_id=WID, workspace_slug="ws", workspace_role="editor"
    )


def _request(state: dict | None = None, headers: dict | None = None):
    return SimpleNamespace(state=SimpleNamespace(**(state or {})), headers=headers or {})


@pytest.fixture
def env(monkeypatch):
    """Patch request + access-token lookups; returns a setter for the current request/token."""
    current = {"request": _request({"user": _user(), "token": "authz.tok"}), "token": None}

    def _get_http_request():
        if current["request"] is None:
            raise RuntimeError("no active HTTP request")
        return current["request"]

    monkeypatch.setattr(mcp_mod, "get_http_request", _get_http_request)
    monkeypatch.setattr(mcp_mod, "get_access_token", lambda: current["token"])
    return current


TOOLS = {"list_notes": set(), "export_notes": {AGENT_GATED}}


async def _call(role: _RoleStub, name: str, tools=TOOLS, service_name="team-notes"):
    gate = DuarAuthMiddleware(SimpleNamespace(service_name=service_name, roles=role))
    nxt = _CallNext()
    result = await gate.on_call_tool(_Ctx(name, _Server(tools)), nxt)
    return result, nxt


# ── gate ───────────────────────────────────────────────────────────────


async def test_ungated_tool_runs_without_duar_call(env):
    role = _RoleStub()
    result, nxt = await _call(role, "list_notes")
    assert result == "TOOL_RESULT" and nxt.called == 1
    assert role.calls == []


async def test_gated_tool_allowed(env):
    role = _RoleStub(allowed=True)
    result, nxt = await _call(role, "export_notes")
    assert role.calls == [("authz.tok", "mcp:team-notes.export_notes", WID)]
    assert nxt.called == 1


async def test_gated_tool_denied(env):
    role = _RoleStub(allowed=False)
    with pytest.raises(ToolError, match="Not permitted: 'export_notes'"):
        await _call(role, "export_notes")


async def test_gated_pdp_error_fails_closed(env):
    with pytest.raises(ToolError, match="authorization unavailable"):
        await _call(_RoleStub(raises=True), "export_notes")


async def test_gate_is_live_per_call(env):
    role = _RoleStub(allowed=True)
    gate = DuarAuthMiddleware(SimpleNamespace(service_name="team-notes", roles=role))
    nxt = _CallNext()
    await gate.on_call_tool(_Ctx("export_notes", _Server(TOOLS)), nxt)
    role.allowed = False
    with pytest.raises(ToolError, match="Not permitted"):
        await gate.on_call_tool(_Ctx("export_notes", _Server(TOOLS)), nxt)
    assert nxt.called == 1 and len(role.calls) == 2


async def test_unauthenticated_denied(env):
    env["request"] = _request()
    env["token"] = None
    with pytest.raises(ToolError, match="no authenticated Duar user"):
        await _call(_RoleStub(), "list_notes")


async def test_stateful_session_refused(env):
    env["request"] = _request({"user": _user(), "token": "authz.tok"}, {"mcp-session-id": "abc"})
    with pytest.raises(ToolError, match="stateless_http=True"):
        await _call(_RoleStub(), "list_notes")


async def test_jwt_mode_fallback_uses_access_token_claims(env):
    env["request"] = None
    env["token"] = SimpleNamespace(token="raw.jwt", claims={"sub": "u1", "wid": str(WID)})  # noqa: S106
    role = _RoleStub(allowed=True)
    await _call(role, "export_notes")
    assert role.calls == [("raw.jwt", "mcp:team-notes.export_notes", WID)]


async def test_unknown_tool_denied(env):
    with pytest.raises(ToolError, match="unknown tool"):
        await _call(_RoleStub(), "no_such_tool")


async def test_missing_fastmcp_context_denied(env):
    gate = DuarAuthMiddleware(SimpleNamespace(service_name="team-notes", roles=_RoleStub()))
    with pytest.raises(ToolError, match="unknown tool"):
        await gate.on_call_tool(_Ctx("list_notes", None), _CallNext())


# ── naming ─────────────────────────────────────────────────────────────


def test_agent_action_unique_per_app():
    assert agent_action("app-a", "t") == "mcp:app-a.t"
    assert agent_action("app-a", "t") != agent_action("app-b", "t")


# ── sync_tools ─────────────────────────────────────────────────────────


def _server_with(gated_name: str = "export_notes") -> FastMCP:
    mcp = FastMCP("x")

    @mcp.tool
    def list_notes() -> list:
        """List notes."""
        return []

    @mcp.tool(name=gated_name, tags={AGENT_GATED})
    def gated() -> dict:
        """Export all notes."""
        return {}

    return mcp


async def test_sync_registers_only_gated_tools():
    role = _RoleStub()
    names = await sync_tools(_server_with(), SimpleNamespace(service_name="team-notes", roles=role))
    assert names == ["mcp:team-notes.export_notes"]
    assert role.registered == [[{"action": "mcp:team-notes.export_notes", "description": "Export all notes."}]]


async def test_sync_uses_service_name_not_realm_scope():
    role = _RoleStub(service_name="realm-x")
    names = await sync_tools(_server_with(), SimpleNamespace(service_name="app-a", roles=role))
    assert names == ["mcp:app-a.export_notes"]


async def test_sync_rejects_out_of_grammar_tool_name():
    role = _RoleStub()
    with pytest.raises(ValueError, match="ListNotes"):
        await sync_tools(_server_with("ListNotes"), SimpleNamespace(service_name="team-notes", roles=role))
    assert role.registered == []


async def test_sync_noop_without_gated_tools():
    mcp = FastMCP("x")

    @mcp.tool
    def list_notes() -> list:
        return []

    role = _RoleStub()
    assert await sync_tools(mcp, SimpleNamespace(service_name="team-notes", roles=role)) == []
    assert role.registered == []
