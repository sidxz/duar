# Team Notes — Duar AuthZ Mode Demo

> **Mode:** This demo uses **AuthZ mode** (default) where the client app authenticates
> users directly with an IdP (e.g. Google Sign-In). Duar validates the IdP token
> and issues an authorization-only JWT. The backend validates both tokens on every request.

A note-taking API that demonstrates Duar's dual-token architecture:

- **Dual-Token Auth** — IdP token (identity) + authz token (authorization) on every request
- **Token Binding** — `idp_sub` in authz token must match `sub` in IdP token
- **Workspace Roles** — editors can create notes, admins can delete them
- **Entity ACLs** — Zanzibar-style per-resource permissions
- **Custom RBAC** — `notes:export` action enforced via service actions

## Architecture

```
                      Google JWKS
                          |
            +-------------+-------------+
            |             |             |
            v             v             v
        Client App    Duar    Demo Backend
            |             |             |
       authenticates  validates     validates
        with Google   IdP token     both tokens
            |             |             |
            |  +----------+             |
            |  | issues authz JWT       |
            v  v                        |
        Holds both tokens ----------> AuthzMiddleware:
        (IdP + authz)                 1. IdP token (Google's key)
                                      2. Authz JWT (Duar's key)
                                      3. idp_sub binding check
```

## Prerequisites

- Duar identity service running locally (`make start`)
- Google OAuth credentials configured in `service/.env`
- Python 3.12+

## Setup

### 1. Start Duar

```bash
# From the repo root
make setup    # First-time only
make start    # Start on :9003
```

### 2. Register a Service App

In the Duar admin panel (http://localhost:9004 -> Service Apps -> Register Service), create a service app with service name `team-notes`. Copy the generated API key.

### 3. Start the Demo Backend

```bash
cd demo-authz/backend
cp .env.example .env
# Edit .env and paste the SERVICE_API_KEY

uv sync
uv run python -m src.main
# or
uv run uvicorn src.main:app --port 9200 --reload
```

## Testing with curl

### Step 1: Get an IdP token

Authenticate with Google (or any configured IdP) to get an ID token. For development, you can use the [Google OAuth Playground](https://developers.google.com/oauthplayground/).

### Step 2: Resolve authorization

```bash
# Exchange IdP token for authz context
curl -X POST http://localhost:9003/authz/resolve \
  -H "X-Service-Key: sk_..." \
  -H "Content-Type: application/json" \
  -d '{
    "idp_token": "eyJ...(your Google ID token)",
    "provider": "google"
  }'

# Response (no workspace_id): returns workspace list
# {
#   "user": {"id": "...", "email": "alice@acme.com", "name": "Alice"},
#   "workspaces": [{"id": "...", "name": "Acme Corp", "slug": "acme", "role": "editor"}]
# }

# With workspace_id: returns signed authz JWT
curl -X POST http://localhost:9003/authz/resolve \
  -H "X-Service-Key: sk_..." \
  -H "Content-Type: application/json" \
  -d '{
    "idp_token": "eyJ...",
    "provider": "google",
    "workspace_id": "uuid-from-above"
  }'

# Response:
# {
#   "user": {"id": "...", "email": "alice@acme.com", "name": "Alice"},
#   "workspace": {"id": "...", "slug": "acme", "role": "editor"},
#   "authz_token": "eyJ...",
#   "expires_in": 300
# }
```

### Step 3: Call the backend with both tokens

```bash
# Send both tokens on every request
curl http://localhost:9200/me \
  -H "Authorization: Bearer eyJ...(IdP token)" \
  -H "X-Authz-Token: eyJ...(authz token from step 2)"

curl http://localhost:9200/notes \
  -H "Authorization: Bearer eyJ...(IdP token)" \
  -H "X-Authz-Token: eyJ...(authz token)"

curl -X POST http://localhost:9200/notes \
  -H "Authorization: Bearer eyJ...(IdP token)" \
  -H "X-Authz-Token: eyJ...(authz token)" \
  -H "Content-Type: application/json" \
  -d '{"title": "My Note", "content": "Hello from AuthZ mode"}'
```

## MCP Tools (Duar MCP)

The backend also serves MCP tools at **`http://localhost:9200/mcp/`**, behind the same Duar
middleware as the REST routes (`mount_mcp` in `src/main.py`, tools in `src/mcp_tools.py`).
An MCP caller sends the user's IdP token plus `X-Workspace-Id`; the middleware mints the
authz token itself (`auto_resolve=True` in `src/config.py`).

| Tool | Gated | Who can call it |
|------|-------|-----------------|
| `list_notes` | no | anyone who can use the workspace — the user's own permissions |
| `export_notes` | **agent-gated** | needs `notes:export` **and** a grant on `mcp:team-notes.export_notes` |

To allow `export_notes` for agents, add `mcp:team-notes.export_notes` (and `notes:export`) to a
workspace role in the admin panel (Workspaces → *your workspace* → Roles) and assign it. The
action is registered automatically when the backend starts.

`GET /agent/tools/{name}` shows the caller side: a backend calling its MCP tools as the
signed-in user with `forward_headers(request)`. Call it with the usual dual-token headers:

```bash
curl http://localhost:9200/agent/tools/list_notes \
  -H "Authorization: Bearer eyJ...(IdP token)" \
  -H "X-Authz-Token: eyJ...(authz token)"

# 403 "Not permitted" until the agent grant above exists
curl http://localhost:9200/agent/tools/export_notes \
  -H "Authorization: Bearer eyJ...(IdP token)" \
  -H "X-Authz-Token: eyJ...(authz token)"
```

## SDK Features Used

| Feature | File | Usage |
|---------|------|-------|
| `Duar(mode="authz")` | `src/config.py` | AuthZ mode with IdP public key |
| `AuthzMiddleware` | `src/main.py` | Validates dual tokens via `duar.protect()` |
| `AuthzClient` | via `duar.authz` | Available for calling `/authz/resolve` |
| `get_current_user` | `src/routes.py` | Extracts user from validated tokens |
| `get_workspace_id` | `src/routes.py` | Scopes note list to workspace |
| `require_role()` | `src/routes.py` | Enforces editor/admin roles |
| `require_action()` | `src/routes.py` | Enforces RBAC action on export |
| `PermissionClient` | `src/routes.py` | Entity-level resource registration |
| `mount_mcp()` | `src/main.py` | Serves the MCP tools at `/mcp/` (stateless, Duar gate, startup registration) |
| `duar.request_auth()` | `src/mcp_tools.py` | The user's auth context inside an MCP tool |
| `AGENT_GATED` | `src/mcp_tools.py` | Marks `export_notes` as needing an agent grant |
| `forward_headers()` | `src/agent_routes.py` | Calls MCP tools as the signed-in user |

## Security Properties

| Property | AuthZ Mode | Proxy Mode |
|----------|-----------|------------|
| Duar key compromise | Privilege escalation only | Full identity forgery |
| Attacker needs real IdP account? | Yes | No |
| Attacker traceable? | Yes (real IdP identity) | No |
| Custom auth flow code in Duar | None | ~400 lines |
