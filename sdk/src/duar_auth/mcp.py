"""Duar MCP — authorize FastMCP tool calls against Duar (human-driven, dual auth).

The MCP server is the PEP. Pair FastMCP's ``JWTVerifier`` (gate 1 — authenticate the
user's ``duar:access`` token against Duar's JWKS) with ``DuarAuthMiddleware`` (gate 2 —
ask Duar whether *this user* may call *this tool* in their workspace). Fail-closed: any
missing identity or PDP error denies the call.

    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.jwt import JWTVerifier
    from duar_auth import RoleClient, PermissionClient
    from duar_auth.mcp import DuarAuthMiddleware, duar_resource, sync_tools

    auth = JWTVerifier(
        jwks_uri=f"{DUAR_URL}/.well-known/jwks.json",
        issuer=DUAR_URL,            # must equal Duar base_url byte-for-byte
        audience="duar:access",
    )
    mcp = FastMCP("chem-mcp", auth=auth)
    roles = RoleClient(DUAR_URL, service_name="chem-mcp", service_key=SERVICE_KEY)
    perms = PermissionClient(DUAR_URL, service_name="chem-mcp", service_key=SERVICE_KEY)
    mcp.add_middleware(DuarAuthMiddleware(roles, perms))
    # at startup: await sync_tools(mcp, roles)

Guardrail: use ``JWTVerifier`` (token verification only). Do NOT use FastMCP's OAuth /
``RemoteAuthProvider`` — it advertises resource-server discovery and would push Duar
toward being an OAuth authorization server.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

from duar_auth.permissions import PermissionClient
from duar_auth.roles import RoleClient

# Tool name -> (resource_type, extractor, permission_action). Process-global: one
# MCP server per process is the expected deployment. Two servers in one process that
# register a same-named resource-scoped tool would share the last binding — a known,
# documented v1 limitation.
_RESOURCE_BINDINGS: dict[str, tuple[str, Callable[[Mapping[str, Any]], Any], str]] = {}

ResourceExtractor = Callable[[Mapping[str, Any]], Any]


def duar_resource(
    resource_type: str,
    extractor: ResourceExtractor,
    *,
    action: str = "access",
    tool_name: str | None = None,
) -> Callable[[Callable], Callable]:
    """Declare a FastMCP tool as resource-scoped.

    After the RBAC action check passes, ``DuarAuthMiddleware`` runs an entity-ACL
    check (``PermissionClient.can``) on the resource the ``extractor`` pulls from the
    tool's arguments. RBAC-only tools need no decorator.

    Apply it BELOW ``@mcp.tool`` so it wraps the underlying function; the binding is
    keyed by the tool name, which defaults to the function name (pass ``tool_name`` if
    you gave ``@mcp.tool`` a custom ``name``)::

        @mcp.tool
        @duar_resource("document", lambda args: args["doc_id"])
        def edit_document(doc_id: str, body: str) -> str: ...
    """

    def decorator(func: Callable) -> Callable:
        name = tool_name or getattr(func, "__name__", None)
        if name:
            _RESOURCE_BINDINGS[name] = (resource_type, extractor, action)
        func.__duar_resource__ = (resource_type, extractor, action)  # type: ignore[attr-defined]
        return func

    return decorator


class DuarAuthMiddleware(Middleware):
    """FastMCP middleware that authorizes every tool call against Duar.

    ``permission_client`` is required only if any tool is resource-scoped via
    ``@duar_resource``; omit it for RBAC-only servers.
    """

    def __init__(
        self,
        role_client: RoleClient,
        permission_client: PermissionClient | None = None,
    ) -> None:
        self.role_client = role_client
        self.permission_client = permission_client

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[override]
        tool_name = context.message.name

        token = get_access_token()
        claims = getattr(token, "claims", None) or {}
        sub = claims.get("sub")
        wid = claims.get("wid")
        if token is None or not sub or not wid:
            raise ToolError(f"Not permitted: no authenticated Duar user for '{tool_name}'")
        try:
            workspace_id = uuid.UUID(str(wid))
        except (ValueError, TypeError) as exc:
            raise ToolError(f"Not permitted: invalid workspace for '{tool_name}'") from exc
        raw_token = token.token

        # Gate 2a — RBAC action. Fail closed on any PDP error.
        try:
            allowed = await self.role_client.check_action(
                token=raw_token, action=tool_name, workspace_id=workspace_id
            )
        except Exception as exc:
            raise ToolError(
                f"Not permitted: authorization unavailable for '{tool_name}'"
            ) from exc
        if not allowed:
            raise ToolError(f"Not permitted: '{tool_name}'")

        # Gate 2b — entity ACL for resource-scoped tools.
        binding = _RESOURCE_BINDINGS.get(tool_name)
        if binding is not None:
            resource_type, extractor, perm_action = binding
            args = getattr(context.message, "arguments", None) or {}
            try:
                resource_id = extractor(args)
            except Exception as exc:
                raise ToolError(
                    f"Not permitted: could not resolve resource for '{tool_name}'"
                ) from exc
            if self.permission_client is None:
                raise ToolError(
                    f"Not permitted: resource check unavailable for '{tool_name}' "
                    "(no PermissionClient configured)"
                )
            try:
                ok = await self.permission_client.can(
                    raw_token, resource_type, resource_id, perm_action
                )
            except Exception as exc:
                raise ToolError(
                    f"Not permitted: authorization unavailable for '{tool_name}'"
                ) from exc
            if not ok:
                raise ToolError(
                    f"Not permitted: '{tool_name}' on {resource_type}:{resource_id}"
                )

        return await call_next(context)


async def sync_tools(mcp, role_client: RoleClient) -> list[str]:
    """Register every tool the FastMCP server exposes as a bare Duar RBAC action.

    Idempotent upsert via the existing ``register_actions`` (no prune, no collision
    rejection in v1 — last-wins). Uses the registration-time tool set
    (``run_middleware=False``) so an auth/visibility middleware can't hide tools from
    the catalog. Returns the synced tool names.
    """
    tools = await mcp.list_tools(run_middleware=False)
    actions: list[dict] = []
    for t in tools:
        entry: dict = {"action": t.name}
        description = getattr(t, "description", None)
        if description:
            entry["description"] = description
        actions.append(entry)
    if actions:
        await role_client.register_actions(actions)
    return [t.name for t in tools]
