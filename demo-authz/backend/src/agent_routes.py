"""Caller example: an app backend calling MCP tools as the signed-in user.

Stands in for a colleague's app whose agent calls Team Notes' tools. The request
already passed Duar's middleware, so ``forward_headers`` builds the IdP token +
verified workspace from that state. One MCP client per request (an agent turn), and
the destination is the fixed ``settings.mcp_url`` — never derived from input.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from duar_auth import forward_headers
from src.config import settings

router = APIRouter()

AGENT_TOOLS = {"list_notes", "export_notes"}


async def call_tool(name: str, args: dict, headers: dict) -> Any:
    async with Client(
        StreamableHttpTransport(settings.mcp_url, headers=headers)
    ) as client:
        result = await client.call_tool(name, args)
    return result.data


@router.get("/agent/tools/{name}")
async def agent_tool(name: str, request: Request):
    """Call one of Team Notes' MCP tools as the signed-in user."""
    if name not in AGENT_TOOLS:
        raise HTTPException(status_code=404, detail="Unknown tool")
    try:
        return await call_tool(name, {}, forward_headers(request))
    except ToolError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
