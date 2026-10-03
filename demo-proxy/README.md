# Team Notes — Duar SDK Demo (Proxy Mode)

> **Mode:** This demo uses **proxy mode** (`mode="proxy"`) where Duar handles
> the entire OAuth flow — redirect, callback, token exchange, JWT issuance.
> For the recommended AuthZ mode, see [`demo-authz/`](../demo-authz/).

A simple note-taking app that demonstrates all features of the Duar SDK:

- **JWT Authentication** — middleware validates RS256 tokens on every request
- **Workspace Isolation** — notes are scoped to the active workspace
- **Workspace Roles** — editors can create notes, admins can delete them
- **Entity ACLs** — individual notes have view/edit permissions via the Zanzibar-style permission system
- **Custom RBAC** — "notes:export" action enforced via registered service actions
- **Resource Sharing** — note owners can share with other users

## Architecture

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Demo Frontend   │────▶│  Demo Backend    │────▶│ Identity Service │
│  React SPA       │     │  FastAPI + SDK   │     │  Auth + Perms    │
│  :9101           │     │  :9100           │     │  :9003           │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

## Prerequisites

- Identity service running locally (see [Getting Started](../docs/getting-started/))
- Google OAuth credentials configured in `service/.env`
- Python 3.12+ and Node.js 18+

## Setup

### 1. Configure the Identity Service

Set the frontend URL and CORS for the demo app:

```bash
# In service/.env, add or update:
FRONTEND_URL=http://localhost:9101
CORS_ORIGINS=http://localhost:3000,http://localhost:9101
```

### 2. Start the Identity Service

```bash
# From the repo root
make setup    # First-time: generates keys, certs, env files, starts Postgres + Redis
make start    # Start the identity service on :9003
```

### 3. Register a Service App (optional)

In the Duar admin panel ([http://localhost:9004](http://localhost:9004) → Service Apps → Register Service), create a service app with service name `team-notes`. Copy the generated API key.

### 4. Start the Demo Backend

```bash
cd demo-proxy/backend
cp .env.example .env
# Edit .env and paste the SERVICE_API_KEY from step 3

uv sync
uv run python -m src.main
# or
uv run uvicorn src.main:app --port 9100 --reload
```

The backend fetches the signing key automatically from Duar's JWKS endpoint — no PEM file distribution needed.

### 5. Start the Demo Frontend

```bash
cd demo-proxy/frontend
npm install
npm run dev
```

### 6. Open the App

Visit [http://localhost:9101](http://localhost:9101) and sign in with Google.

## What to Try

1. **Sign in** — OAuth flow through the identity service, workspace selection, JWT token exchange
2. **Create a note** — requires `editor` role (demonstrates `require_role("editor")`)
3. **View a note** — checks entity-level `view` permission (demonstrates `permissions.can()`)
4. **Edit a note** — checks entity-level `edit` permission
5. **Share a note** — owner can share with another user via the permission system
6. **Delete a note** — requires `admin` role (demonstrates `require_role("admin")`)
7. **Export notes** — requires `notes:export` RBAC action (demonstrates `require_action()`)

## SDK Features Used

| Feature | File | Usage |
|---------|------|-------|
| `JWTAuthMiddleware` | `backend/src/main.py` | Validates Bearer tokens via JWKS auto-discovery |
| `get_current_user` | `backend/src/routes.py` | Extracts user from JWT in `/me` |
| `get_workspace_id` | `backend/src/routes.py` | Scopes note list to workspace |
| `require_role()` | `backend/src/routes.py` | Enforces editor/admin roles on create/delete |
| `require_action()` | `backend/src/routes.py` | Enforces RBAC action on export |
| `PermissionClient` | `backend/src/routes.py` | Entity-level view/edit checks and resource registration |
| `RoleClient` | `backend/src/main.py` | Registers service actions on startup |
