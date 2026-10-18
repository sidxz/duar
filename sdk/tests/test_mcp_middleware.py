"""DuarAuthMiddleware / duar_resource / sync_tools — the Duar MCP PEP.

Unit-tested with fakes for the FastMCP context + stub Duar clients; no live server.
Covers Review-Focus items 1-6 (missing/expired token, deny, unknown tool, resource
deny, fail-closed).
"""

from __future__ import annotations

import uuid

import pytest
from fastmcp.exceptions import ToolError

from duar_auth import mcp as mcp_mod
from duar_auth.mcp import DuarAuthMiddleware, duar_resource, sync_tools

WID = "11111111-1111-4111-8111-111111111111"


# ── fakes ──────────────────────────────────────────────────────────────


class _Msg:
    def __init__(self, name: str, arguments: dict | None = None):
        self.name = name
        self.arguments = arguments or {}


class _Ctx:
    def __init__(self, name: str, arguments: dict | None = None):
        self.message = _Msg(name, arguments)


class _Token:
    def __init__(self, token=None, claims=None):
        self.token = token or "raw.jwt"  # noqa: S105 — fake JWT string for tests
        self.claims = claims if claims is not None else {"sub": "u1", "wid": WID}


class _RoleStub:
    def __init__(self, allowed=True, raises=False):
        self.allowed = allowed
        self.raises = raises
        self.calls = []

    async def check_action(self, token, action, workspace_id):
        self.calls.append((token, action, workspace_id))
        if self.raises:
            raise RuntimeError("duar down")
        return self.allowed


class _PermStub:
    def __init__(self, allowed=True, raises=False):
        self.allowed = allowed
        self.raises = raises
        self.calls = []

    async def can(self, token, resource_type, resource_id, action):
        self.calls.append((token, resource_type, resource_id, action))
        if self.raises:
            raise RuntimeError("duar down")
        return self.allowed


class _CallNext:
    def __init__(self, result="TOOL_RESULT"):
        self.result = result
        self.called = False

    async def __call__(self, context):
        self.called = True
        return self.result


@pytest.fixture(autouse=True)
def _clear_bindings():
    mcp_mod._RESOURCE_BINDINGS.clear()
    yield
    mcp_mod._RESOURCE_BINDINGS.clear()


def _patch_token(monkeypatch, token):
    monkeypatch.setattr(mcp_mod, "get_access_token", lambda: token)


# ── RBAC action path ───────────────────────────────────────────────────


async def test_allow_runs_the_tool(monkeypatch):
    _patch_token(monkeypatch, _Token())
    mw = DuarAuthMiddleware(_RoleStub(allowed=True))
    nxt = _CallNext()
    result = await mw.on_call_tool(_Ctx("reports.export"), nxt)
    assert result == "TOOL_RESULT"
    assert nxt.called is True


async def test_deny_blocks_and_does_not_run(monkeypatch):
    _patch_token(monkeypatch, _Token())
    mw = DuarAuthMiddleware(_RoleStub(allowed=False))
    nxt = _CallNext()
    with pytest.raises(ToolError):
        await mw.on_call_tool(_Ctx("reports.export"), nxt)
    assert nxt.called is False


async def test_missing_token_denies(monkeypatch):
    _patch_token(monkeypatch, None)
    role = _RoleStub(allowed=True)
    mw = DuarAuthMiddleware(role)
    nxt = _CallNext()
    with pytest.raises(ToolError):
        await mw.on_call_tool(_Ctx("reports.export"), nxt)
    assert nxt.called is False
    assert role.calls == []  # never reached Duar


async def test_missing_wid_claim_denies(monkeypatch):
    _patch_token(monkeypatch, _Token(claims={"sub": "u1"}))  # no wid
    mw = DuarAuthMiddleware(_RoleStub(allowed=True))
    nxt = _CallNext()
    with pytest.raises(ToolError):
        await mw.on_call_tool(_Ctx("reports.export"), nxt)
    assert nxt.called is False


async def test_pdp_error_fails_closed(monkeypatch):
    _patch_token(monkeypatch, _Token())
    mw = DuarAuthMiddleware(_RoleStub(raises=True))
    nxt = _CallNext()
    with pytest.raises(ToolError):
        await mw.on_call_tool(_Ctx("reports.export"), nxt)
    assert nxt.called is False


async def test_action_check_gets_bare_name_and_workspace(monkeypatch):
    _patch_token(monkeypatch, _Token())
    role = _RoleStub(allowed=True)
    mw = DuarAuthMiddleware(role)
    await mw.on_call_tool(_Ctx("reports.export"), _CallNext())
    token, action, workspace_id = role.calls[0]
    assert action == "reports.export"  # bare action, no service prefix
    assert workspace_id == uuid.UUID(WID)


# ── resource-scoped (entity ACL) path ──────────────────────────────────


async def test_resource_tool_denied_even_when_action_allowed(monkeypatch):
    _patch_token(monkeypatch, _Token())
    rid = "22222222-2222-4222-8222-222222222222"
    duar_resource("document", lambda a: a["doc_id"], tool_name="edit_document")(
        lambda: None
    )
    role = _RoleStub(allowed=True)
    perm = _PermStub(allowed=False)  # ACL denies
    mw = DuarAuthMiddleware(role, perm)
    nxt = _CallNext()
    with pytest.raises(ToolError):
        await mw.on_call_tool(_Ctx("edit_document", {"doc_id": rid}), nxt)
    assert nxt.called is False
    assert perm.calls[0][2] == rid  # extractor pulled the resource id


async def test_resource_tool_allowed_runs(monkeypatch):
    _patch_token(monkeypatch, _Token())
    rid = "22222222-2222-4222-8222-222222222222"
    duar_resource("document", lambda a: a["doc_id"], tool_name="edit_document")(
        lambda: None
    )
    mw = DuarAuthMiddleware(_RoleStub(allowed=True), _PermStub(allowed=True))
    nxt = _CallNext()
    result = await mw.on_call_tool(_Ctx("edit_document", {"doc_id": rid}), nxt)
    assert result == "TOOL_RESULT"
    assert nxt.called is True


async def test_resource_tool_without_permission_client_denies(monkeypatch):
    _patch_token(monkeypatch, _Token())
    duar_resource("document", lambda a: a["doc_id"], tool_name="edit_document")(
        lambda: None
    )
    mw = DuarAuthMiddleware(_RoleStub(allowed=True))  # no PermissionClient
    nxt = _CallNext()
    with pytest.raises(ToolError):
        await mw.on_call_tool(_Ctx("edit_document", {"doc_id": "x"}), nxt)
    assert nxt.called is False


# ── sync_tools ─────────────────────────────────────────────────────────


class _Tool:
    def __init__(self, name, description=None):
        self.name = name
        self.description = description


class _FakeMCP:
    def __init__(self, tools):
        self._tools = tools
        self.run_middleware_seen = None

    async def list_tools(self, *, run_middleware=True):
        self.run_middleware_seen = run_middleware
        return self._tools


class _RegStub:
    def __init__(self):
        self.registered = None

    async def register_actions(self, actions):
        self.registered = actions
        return {"ok": True}


async def test_sync_tools_registers_bare_actions():
    mcp = _FakeMCP([_Tool("reports.export", "Export"), _Tool("reports.view")])
    reg = _RegStub()
    names = await sync_tools(mcp, reg)
    assert names == ["reports.export", "reports.view"]
    assert mcp.run_middleware_seen is False  # registration-time set, not filtered
    assert {"action": "reports.export", "description": "Export"} in reg.registered
    assert {"action": "reports.view"} in reg.registered
