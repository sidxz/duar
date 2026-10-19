"""Team Notes MCP tools — served at /mcp/ by ``mount_mcp`` (see main.py).

Tools act as the signed-in user: ``duar.request_auth(...)`` is the same auth context
the REST routes get, and each tool runs the app's own checks. ``export_notes`` is
agent-gated, so it also needs a Duar grant on ``mcp:<service_name>.export_notes``.
"""

from dataclasses import asdict

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from duar_auth.mcp import AGENT_GATED
from src.config import duar
from src.models import notes

mcp = FastMCP("team-notes")


@mcp.tool
async def list_notes() -> list[dict]:
    """List the notes in your current workspace."""
    auth = duar.request_auth(get_http_request())
    return [asdict(n) for n in notes.list_by_workspace(auth.workspace_id)]


@mcp.tool(tags={AGENT_GATED})
async def export_notes() -> dict:
    """Export all notes in your current workspace (needs notes:export)."""
    auth = duar.request_auth(get_http_request())
    if not await auth.check_action("notes:export"):
        raise ToolError("Not permitted: notes:export")
    workspace_notes = notes.list_by_workspace(auth.workspace_id)
    return {
        "format": "json",
        "count": len(workspace_notes),
        "notes": [asdict(n) for n in workspace_notes],
    }
