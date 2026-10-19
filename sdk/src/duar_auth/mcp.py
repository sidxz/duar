"""Duar MCP — serve MCP tools from a Duar-protected app (human-driven, AuthZ mode).

The app's own Duar middleware authenticates every MCP request (in AuthZ mode with
``auto_resolve=True`` a caller sends ``Authorization: Bearer <IdP token>`` +
``X-Workspace-Id``). Tools then act with the user's own app permissions: inside a
tool, ``duar.request_auth(get_http_request())`` returns the same ``RequestAuth`` the
app's REST routes get, and the tool runs the app's own checks.

Tools a developer marks as agent-gated additionally need a Duar grant on the agent
action ``mcp:<service_name>.<tool_name>``, checked live on every call and recorded by
Duar (visible in the ``/mcp`` console)::

    from fastmcp import FastMCP
    from fastmcp.server.dependencies import get_http_request
    from duar_auth.mcp import AGENT_GATED, mount_mcp

    mcp = FastMCP("team-notes")

    @mcp.tool
    async def list_notes() -> list[dict]:
        auth = duar.request_auth(get_http_request())
        ...

    @mcp.tool(tags={AGENT_GATED})        # also needs mcp:team-notes.export_notes
    async def export_notes() -> dict:
        ...

    mount_mcp(app, mcp, duar)            # /mcp/, stateless, gate, startup registration

Guardrail: authentication is Duar's own middleware (or FastMCP's verify-only
``JWTVerifier`` in JWT mode). Do NOT use FastMCP's OAuth / ``RemoteAuthProvider`` — it
advertises resource-server discovery and would push Duar toward being an OAuth
authorization server.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext

from duar_auth.authz_middleware import AuthzMiddleware
from duar_auth.middleware import JWTAuthMiddleware
from duar_auth.types import DuarError

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from starlette.applications import Starlette

    from duar_auth.duar import Duar

logger = logging.getLogger("duar_auth.mcp")

AGENT_GATED = "duar:agent-gated"

_ACTION_RE = re.compile(r"^[a-z][a-z0-9_.:-]*$")


def agent_action(service_name: str, tool_name: str) -> str:
    """The Duar action an agent-gated tool is checked against: ``mcp:<service>.<tool>``.

    Carries the app's own ``service_name`` (never the realm slug), so same-named tools
    in two apps of one realm stay separate grants.
    """
    action = f"mcp:{service_name}.{tool_name}"
    if not _ACTION_RE.match(action):
        raise ValueError(
            f"MCP tool {tool_name!r} can't be an agent-gated Duar action ({action!r} must match "
            f"{_ACTION_RE.pattern}); rename the tool"
        )
    return action


def _caller(tool_name: str) -> tuple[str, uuid.UUID]:
    """(token, workspace_id) of the authenticated caller, or raise ``ToolError``."""
    try:
        request = get_http_request()
    except RuntimeError:
        request = None
    user = getattr(request.state, "user", None) if request is not None else None
    token = getattr(request.state, "token", None) if request is not None else None
    if user is not None and token:
        # Duar's middleware sets request.state only; the MCP SDK binds a session to
        # scope["user"], so a stateful session here would not be owner-bound.
        if request.headers.get("mcp-session-id"):
            raise ToolError("Not permitted: stateful MCP sessions are not supported; serve with stateless_http=True")
        return token, user.workspace_id

    access = get_access_token()  # JWT mode behind FastMCP's JWTVerifier
    claims = getattr(access, "claims", None) or {}
    if access is not None and claims.get("sub") and claims.get("wid"):
        try:
            return access.token, uuid.UUID(str(claims["wid"]))
        except (ValueError, TypeError) as exc:
            raise ToolError(f"Not permitted: invalid workspace for '{tool_name}'") from exc
    raise ToolError(f"Not permitted: no authenticated Duar user for '{tool_name}'")


class DuarAuthMiddleware(Middleware):
    """FastMCP middleware: every tool call needs an authenticated Duar user; agent-gated
    tools also need a live Duar grant on ``mcp:<service_name>.<tool>``. Fail-closed."""

    def __init__(self, duar: Duar) -> None:
        self.duar = duar

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[override]
        tool_name = context.message.name
        token, workspace_id = _caller(tool_name)

        server = getattr(context.fastmcp_context, "fastmcp", None)
        try:
            tool = await server.get_tool(tool_name)
        except Exception as exc:
            raise ToolError(f"Not permitted: unknown tool '{tool_name}'") from exc

        if AGENT_GATED in (tool.tags or set()):
            action = agent_action(self.duar.service_name, tool_name)
            try:
                allowed = await self.duar.roles.check_action(token, action, workspace_id)
            except Exception as exc:
                raise ToolError(f"Not permitted: authorization unavailable for '{tool_name}'") from exc
            if not allowed:
                raise ToolError(f"Not permitted: '{tool_name}'")

        return await call_next(context)


async def sync_tools(mcp: FastMCP, duar: Duar) -> list[str]:
    """Register the server's agent-gated tools with Duar as ``mcp:<service>.<tool>``.

    Idempotent upsert via ``register_actions`` (no prune). Reads the registration-time
    tool set (``run_middleware=False``). A tool name outside Duar's action grammar
    raises ``ValueError`` before anything is sent. Returns the registered action names.
    """
    tools = await mcp.list_tools(run_middleware=False)
    actions: list[dict] = []
    for t in tools:
        if AGENT_GATED not in (t.tags or set()):
            continue
        entry: dict = {"action": agent_action(duar.service_name, t.name)}
        if t.description:
            entry["description"] = t.description
        actions.append(entry)
    if actions:
        await duar.roles.register_actions(actions)
    return [a["action"] for a in actions]


def mount_mcp(app: Starlette, mcp: FastMCP, duar: Duar, path: str = "/mcp") -> None:
    """Serve ``mcp`` at ``path`` (endpoint ``<path>/``) inside a Duar-protected app.

    Stateless HTTP only, behind the app's own Duar middleware, with the Duar gate
    added and the agent-gated tools registered with Duar at startup — after the
    app's own lifespan (Duar key fetch, realm discovery) and on the server's loop.
    """
    protected = any(m.cls in (AuthzMiddleware, JWTAuthMiddleware) for m in app.user_middleware)
    if not protected:
        raise RuntimeError("call duar.protect(app) before mount_mcp(...)")
    if duar.mode == "authz" and not duar.auto_resolve:
        logger.warning(
            "mount_mcp: Duar(auto_resolve=False) — MCP callers can't authenticate with an IdP token "
            "plus X-Workspace-Id; enable auto_resolve for apps that serve MCP"
        )

    mcp.add_middleware(DuarAuthMiddleware(duar))
    mcp_app = mcp.http_app(path="/", stateless_http=True)
    app.mount(path, mcp_app)

    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a) as state:
            async with mcp_app.lifespan(mcp_app):
                try:
                    await sync_tools(mcp, duar)
                except (DuarError, httpx.HTTPError):
                    logger.warning(
                        "mount_mcp: agent-gated MCP tools not registered with Duar; they deny until registered",
                        exc_info=True,
                    )
                yield state

    app.router.lifespan_context = lifespan
