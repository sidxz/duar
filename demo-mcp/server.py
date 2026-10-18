"""demo-mcp — a FastMCP server authorized by Duar (Duar MCP, human-driven dual auth).

The server is the PEP. FastMCP's ``JWTVerifier`` (gate 1) authenticates the user's
``duar:access`` token against Duar's JWKS; ``DuarAuthMiddleware`` (gate 2) asks Duar
whether *this user* may call *this tool* in their workspace. Tools register as Duar RBAC
actions via ``sync_tools``.

Config (env):
    DUAR_URL            Duar base URL (default http://localhost:9003)
    MCP_SERVICE_NAME    this server's registered service_app name (default demo-mcp)
    DUAR_SERVICE_KEY    the service_app's key (for the dual-auth leg)

Run: see README.md.
"""

from __future__ import annotations

import asyncio
import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier

from duar_auth import PermissionClient, RoleClient
from duar_auth.mcp import DuarAuthMiddleware, duar_resource, sync_tools

DUAR_URL = os.environ.get("DUAR_URL", "http://localhost:9003").rstrip("/")
SERVICE_NAME = os.environ.get("MCP_SERVICE_NAME", "demo-mcp")
SERVICE_KEY = os.environ.get("DUAR_SERVICE_KEY", "")

# Gate 1 — verify-only. NOT an OAuth RemoteAuthProvider (that would advertise
# discovery and push Duar toward being an authorization server). issuer must equal
# Duar's base_url byte-for-byte.
auth = JWTVerifier(
    jwks_uri=f"{DUAR_URL}/.well-known/jwks.json",
    issuer=DUAR_URL,
    audience="duar:access",
)

mcp = FastMCP("demo-mcp", auth=auth)

roles = RoleClient(DUAR_URL, service_name=SERVICE_NAME, service_key=SERVICE_KEY)
perms = PermissionClient(DUAR_URL, service_name=SERVICE_NAME, service_key=SERVICE_KEY)

# Gate 2 — authorize every tool call against Duar (fail-closed).
mcp.add_middleware(DuarAuthMiddleware(roles, perms))


@mcp.tool
def list_reports() -> list[str]:
    """RBAC-only tool: the caller needs the ``list_reports`` action in their workspace."""
    return ["Q1", "Q2", "Q3"]


@mcp.tool
@duar_resource("document", lambda args: args["doc_id"])
def read_document(doc_id: str) -> str:
    """Resource-scoped tool: the caller needs the ``read_document`` action AND read
    access to document ``doc_id`` (entity ACL)."""
    return f"contents of document {doc_id}"


async def register_tools() -> list[str]:
    """Push this server's tools to Duar as RBAC actions (idempotent upsert)."""
    names = await sync_tools(mcp, roles)
    print(f"[demo-mcp] synced tools -> Duar actions under '{SERVICE_NAME}': {names}")
    return names


if __name__ == "__main__":
    asyncio.run(register_tools())
    # streamable-HTTP: each tool call is its own gated request; a refreshed access
    # token simply rides the next call.
    mcp.run(transport="http", host="127.0.0.1", port=9100)
