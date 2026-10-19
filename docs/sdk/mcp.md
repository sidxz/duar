# MCP (Agents)

Duar MCP lets an app expose [MCP](https://modelcontextprotocol.io) tools that AI agents call **on behalf of a signed-in user**. Duar authenticates every call and authorizes it against the same roles and permissions the app already uses; tools you mark as sensitive additionally need an explicit **agent grant**, which Duar records and the admin console's **Duar MCP** section shows.

Duar stays an authentication proxy and policy decision point: MCP callers present the user's existing tokens, and Duar never acts as an OAuth authorization server for MCP clients.

!!! note "Optional extra"
    The MCP helpers need FastMCP: `pip install "duar-auth[mcp]"`. `forward_headers` (for callers) is in the base package.

## Serve MCP from your app (default)

Mount a [FastMCP](https://gofastmcp.com) server inside your existing FastAPI app. It sits behind the same Duar middleware as your REST routes, so tools act with the user's own permissions and run your app's own checks.

```python
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from duar_auth import Duar
from duar_auth.mcp import AGENT_GATED, mount_mcp

duar = Duar(
    base_url=DUAR_URL,
    service_name="team-notes",
    service_key=SERVICE_KEY,
    mode="authz",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience=GOOGLE_CLIENT_ID,
    idp_issuer="https://accounts.google.com",
    auto_resolve=True,          # MCP callers send the IdP token + X-Workspace-Id
    idp_provider="google",
)
app = FastAPI(lifespan=duar.lifespan)
duar.protect(app)

mcp = FastMCP("team-notes", mask_error_details=True)   # never return internal error text to agents


@mcp.tool
async def list_notes() -> list[dict]:
    """List the notes in your current workspace."""
    auth = duar.request_auth(get_http_request())   # same RequestAuth your routes get
    return [asdict(n) for n in notes.list_by_workspace(auth.workspace_id)]


@mcp.tool(tags={AGENT_GATED})
async def export_notes() -> dict:
    """Export all notes (needs notes:export)."""
    auth = duar.request_auth(get_http_request())
    if not await auth.check_action("notes:export"):    # the app's own check, as in REST
        raise ToolError("Not permitted: notes:export")
    ...


mount_mcp(app, mcp, duar)   # call after duar.protect(app)
```

`mount_mcp(app, mcp, duar, path="/mcp")`:

- serves the tools at **`/mcp/`** — configure MCP clients with the trailing slash. A request to `/mcp` gets a
  redirect, and behind a TLS-terminating proxy that redirect can point at `http://` and drop the `Authorization` header,
- always uses **stateless** streamable HTTP — every request carries its own credentials,
- adds the Duar gate (every tool call needs an authenticated user; gated tools need their agent grant),
- runs FastMCP's lifespan inside your app's lifespan, and
- registers your agent-gated tools with Duar at startup, after `duar.lifespan` has fetched keys and discovered the realm.

It raises `RuntimeError` unless the app runs Duar in AuthZ mode with `auto_resolve=True` (callers authenticate with the user's IdP token plus `X-Workspace-Id`), is protected by `duar.protect(app)`, and the FastMCP server is created with `mask_error_details=True`. Masking keeps exception text (hosts, SQL, paths) from reaching agents: a tool's `ToolError` message — including the gate's "Not permitted" — is returned as written, any other exception (an app helper's `HTTPException` too) as a generic tool error.

Inside a tool, `duar.request_auth(get_http_request())` returns the request's `RequestAuth`, so the tool can call the same use cases, `check_action`, `can` and `accessible` your routes use.

## Agent-gated tools

By default an agent can do what the user can do in your app. For tools where you want an explicit agent permission on top — deleting, exporting, anything you would not want an agent to do just because the user could — add `tags={AGENT_GATED}`:

- The tool is checked against the Duar action **`mcp:<service_name>.<tool_name>`** (for example `mcp:team-notes.export_notes`) on every call, live — a revoked grant takes effect on the next call.
- `mount_mcp` registers it at startup. Grant it like any action: add it to a workspace role in the admin panel and assign the role to users or groups.
- Workspace owners and admins get **no automatic pass** — the explicit agent grant is the point.
- The tool's own checks still run, so the effective permission is the agent grant **and** the user's app permission.
- The name embeds your app's `service_name`, so two apps in one [realm](realms.md) can both have an `export_notes` tool without one grant covering both.
- A tool name outside Duar's action grammar (`^[a-z][a-z0-9_.:-]*$`, e.g. `ListNotes`) fails startup with an error naming the tool — rename the tool.

Calls to gated tools appear in the console under **Duar MCP → MCP Apps → *your app* → Tool-call audit**, and in **Usage**.

## Call MCP tools as the user

An app whose backend runs an agent calls another app's MCP endpoint with the signed-in user's identity. Inside a request that already passed your Duar middleware, `forward_headers(request)` returns `Authorization: Bearer <IdP token>` and `X-Workspace-Id: <verified workspace>`, built from verified state — never from raw inbound headers:

```python
from duar_auth import forward_headers
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

DOCS_MCP_URL = "https://docu-store.internal/mcp/"   # fixed config, never from tool args or LLM output


@router.post("/assistant/turn")
async def assistant_turn(request: Request, body: TurnRequest):
    transport = StreamableHttpTransport(DOCS_MCP_URL, headers=forward_headers(request))
    async with Client(transport) as client:          # one client per agent turn
        tools = await client.list_tools()
        ...
        result = await client.call_tool("list_documents", {"limit": 10})
```

- **Open one MCP client per agent turn.** Headers are fixed when the client connects, and a backend cannot refresh a user's IdP token — use the token from the current request.
- **Refuse to start an agent run when the IdP token is close to expiry** (Google ID tokens last about an hour).
- **Never send `X-Authz-Token`** to another app: it is bound to your app and takes precedence, so the target answers 403.
- **Pin MCP server URLs in config.** Never call a URL derived from tool arguments or model output.
- The target app must run with `auto_resolve=True`.

## Security notes

- **Stateless only.** `mount_mcp` serves stateless HTTP, and the gate refuses any call that belongs to a stateful MCP session (such a session would not be bound to its owner).
- **The IdP token is a suite-wide credential.** Every first-party app in the deployment accepts it for any workspace the user belongs to, for its lifetime; Duar logout cannot revoke it. Forward it only between your own apps — never to third parties.
- **Revocation.** Deactivating a user or removing a membership takes effect at the next authz-token mint; gated-tool checks are live on every call; decisions based on the token's workspace role can lag up to the authz-token lifetime.
- **Resolve budget.** Each app mints at most one authz token per user and workspace per few minutes per process, but `/authz/resolve` is limited per calling service (`RATE_LIMIT_AUTHZ_RESOLVE`, default 60/minute) and shared with browser sign-ins. Size it for your user count and worker processes.

See the `demo-authz` app for a complete example: `src/mcp_tools.py`, `src/agent_routes.py`.
