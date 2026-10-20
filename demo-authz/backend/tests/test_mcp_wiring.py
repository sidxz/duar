"""Team Notes serves MCP at /mcp/ and calls its own tools as the signed-in user."""

import uuid

import pytest
from fastapi import HTTPException
from fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.routing import Mount

from duar_auth.mcp import AGENT_GATED
from duar_auth.types import AuthenticatedUser
from src import agent_routes
from src.main import app
from src.mcp_tools import mcp


def _request(workspace_id: uuid.UUID) -> Request:
    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="a@b.c",
        name="A",
        workspace_id=workspace_id,
        workspace_slug="ws",
        workspace_role="editor",
    )
    state = {"user": user, "token": "authz", "idp_token": "idp"}
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": [], "state": state}
    )


@pytest.fixture
def calls(monkeypatch):
    recorded: list[tuple] = []

    async def fake_call_tool(name, args, headers):
        recorded.append((name, args, headers))
        return ["note"]

    monkeypatch.setattr(agent_routes, "call_tool", fake_call_tool)
    return recorded


def test_mcp_mounted():
    assert any(isinstance(r, Mount) and r.path == "/mcp" for r in app.routes)


async def test_tools_and_gating():
    tools = {t.name: t for t in await mcp.list_tools(run_middleware=False)}
    assert set(tools) == {"list_notes", "export_notes"}
    assert AGENT_GATED in tools["export_notes"].tags
    assert AGENT_GATED not in tools["list_notes"].tags


async def test_agent_route_forwards_user_headers(calls):
    w = uuid.uuid4()
    assert await agent_routes.agent_tool("list_notes", _request(w)) == ["note"]
    assert calls == [
        ("list_notes", {}, {"Authorization": "Bearer idp", "X-Workspace-Id": str(w)})
    ]


async def test_agent_route_rejects_unknown_tool(calls):
    with pytest.raises(HTTPException) as exc:
        await agent_routes.agent_tool("delete_everything", _request(uuid.uuid4()))
    assert exc.value.status_code == 404
    assert calls == []


async def test_agent_route_maps_denial_to_403(monkeypatch):
    async def denied(name, args, headers):
        raise ToolError("Not permitted: 'export_notes'")

    monkeypatch.setattr(agent_routes, "call_tool", denied)
    with pytest.raises(HTTPException) as exc:
        await agent_routes.agent_tool("export_notes", _request(uuid.uuid4()))
    assert exc.value.status_code == 403
    assert "Not permitted" in exc.value.detail
