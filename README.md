# Duar

![Duar](docs/assets/images/splash.png)

**DUAR** — **D**oorway for **U**sers, **A**ctions, and **R**esources.

An authentication proxy and authorization microservice. Duar handles OAuth2/OIDC authentication from external IdPs, multi-tenant workspace management, and fine-grained Zanzibar-style permissions so you can focus on your application logic.

Ships with an Admin UI, Python SDK, and JS/TS SDK (React, Next.js).

## Status

[![CI](https://github.com/sidxz/duar/actions/workflows/ci.yml/badge.svg)](https://github.com/sidxz/duar/actions/workflows/ci.yml)
[![Docs](https://github.com/sidxz/duar/actions/workflows/docs.yml/badge.svg)](https://sidxz.github.io/duar/)
[![PyPI](https://img.shields.io/pypi/v/duar-auth?logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/duar-auth/)
[![npm](https://img.shields.io/npm/v/@duar-auth/js?logo=npm&logoColor=white&label=npm)](https://www.npmjs.com/package/@duar-auth/js)
[![Docker](https://img.shields.io/badge/Docker-ghcr.io/sidxz/duar-2496ed?logo=docker&logoColor=white)](https://github.com/sidxz/duar/pkgs/container/duar)
[![AI Assisted](https://img.shields.io/badge/AI%20Co--pilot-Claude%20Code-6f42c1?logo=anthropic&logoColor=white)](https://claude.ai/claude-code)
[![Python](https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169e1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-dc382d?logo=redis&logoColor=white)](https://redis.io/)

## Two Integration Modes

**AuthZ mode (recommended)** — Your app handles IdP login directly (Google, GitHub, EntraID). Duar validates the IdP token and issues an authorization JWT. Dual-token design with `idp_sub` binding for security.

**Proxy mode** — Duar handles the full OAuth2/OIDC flow. Your app receives a single Duar-issued JWT. Simpler setup, but Duar sits in the login path.

## Capabilities

- **OAuth2/OIDC authentication** from Google, GitHub, Microsoft EntraID, or any OIDC provider
- **Three-tier authorization** — workspace roles (JWT claims), custom RBAC (DB), and entity ACLs (Zanzibar-style)
- **Multi-tenant workspaces** — users, groups, roles, and permissions are scoped per workspace
- **Token lifecycle** — RS256 JWTs, refresh rotation, reuse detection, Redis denylist
- **Admin panel** — React SPA for managing workspaces, users, roles, permissions, and service apps
- **Python SDK** — `pip install duar-auth` with middleware, FastAPI dependencies, and HTTP clients
- **JS/TS SDK** — `@duar-auth/js`, `@duar-auth/react`, `@duar-auth/nextjs` for browser, React, and Next.js
- **DDD support** — `RequestAuth` bridge for clean architecture with framework-agnostic `AuthContext` protocol
- **JWKS endpoint** — `/.well-known/jwks.json` for automatic key discovery
- **Security hardened** — rate limiting, CORS, HSTS, CSP, trusted hosts, session encryption

> **BETA SOFTWARE WARNING**
> This software is currently in beta and **not fully production ready**. While functional and actively developed, it may contain bugs, incomplete features, or breaking changes. Use in production environments at your own risk. Contributions and feedback are welcome!

## Documentation

Full documentation at [sidxz.github.io/duar](https://sidxz.github.io/duar/)

## Architecture

### AuthZ Mode (recommended)

Your app handles IdP login. Duar validates the IdP token and issues an authorization JWT.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#dc2626', 'primaryTextColor': '#fff', 'primaryBorderColor': '#b91c1c', 'lineColor': '#dc2626', 'secondaryColor': '#fecaca', 'tertiaryColor': '#fee2e2', 'actorBkg': '#dc2626', 'actorTextColor': '#fff', 'actorBorder': '#b91c1c', 'signalColor': '#dc2626', 'labelBoxBkgColor': '#fee2e2', 'noteBkgColor': '#fecaca'}}}%%
sequenceDiagram
    participant U as User
    participant F as Your Frontend
    participant IdP as Google / GitHub
    participant S as Duar
    participant B as Your Backend

    U->>F: Click "Sign in"
    F->>IdP: OAuth2 login
    IdP-->>F: IdP token
    F->>S: POST /authz/resolve (IdP token)
    S->>IdP: Validate token (JWKS)
    S-->>F: Authz JWT
    F->>B: API request (IdP + authz tokens)
    B->>S: Check permissions / roles
    S-->>B: Allowed / denied
    B-->>F: Response
```

### Proxy Mode

Duar handles the full OAuth flow. Your app receives a single JWT.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#dc2626', 'primaryTextColor': '#fff', 'primaryBorderColor': '#b91c1c', 'lineColor': '#dc2626', 'secondaryColor': '#fecaca', 'tertiaryColor': '#fee2e2', 'actorBkg': '#dc2626', 'actorTextColor': '#fff', 'actorBorder': '#b91c1c', 'signalColor': '#dc2626', 'labelBoxBkgColor': '#fee2e2', 'noteBkgColor': '#fecaca'}}}%%
sequenceDiagram
    participant U as User
    participant F as Your Frontend
    participant S as Duar
    participant IdP as Google / GitHub
    participant B as Your Backend

    U->>F: Click "Sign in"
    F->>S: Redirect to /auth/login
    S->>IdP: OAuth2/OIDC
    IdP-->>S: Callback
    S-->>F: Access JWT + refresh token
    F->>B: API request (Bearer JWT)
    B->>S: Check permissions / roles
    S-->>B: Allowed / denied
    B-->>F: Response
```

## Authorization Model

| Tier | Mechanism | Granularity | Example |
|------|-----------|-------------|---------|
| **Workspace Roles** | JWT claims | Coarse | "Is user an editor in this workspace?" |
| **Custom RBAC** | DB roles + actions | Action-level | "Can user export reports?" |
| **Entity ACLs** | Zanzibar-style DB | Per-resource | "Can user edit document X?" |

## Quick Start

### From source

```bash
git clone <repo-url> && cd identity-service
make setup    # generates JWT keys, TLS certs, .env files, installs deps, starts Postgres + Redis
```

`make setup` is idempotent. Once complete, configure an OAuth provider:

```bash
vim service/.env    # add GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET (or GitHub/EntraID), ADMIN_EMAILS
```

Then start:

```bash
make start    # identity service on :9003 (auto-migrates DB on boot)
make admin    # admin panel on :9004
make seed     # (optional) populate test data
```

### Docker

```bash
make setup
vim .env.prod           # set BASE_URL, ADMIN_URL, OAuth creds, ADMIN_EMAILS
docker swarm init    # once per host (overlay network + secrets need swarm mode)
docker stack deploy -c docker-compose.prod.yml duar
```

### Next steps

1. Sign in to the **admin panel** (`http://localhost:9004`) — your `ADMIN_EMAILS` user is auto-promoted on first login.
2. Register a **service app** (API key for your backend + allowed origins for your frontend).
3. Integrate using the [Python SDK](#python-sdk) or [JS/TS SDK](#jsts-sdk).

See the [Getting Started guide](https://sidxz.github.io/duar/getting-started/) for the full walkthrough.

## Python SDK

```bash
pip install duar-auth
```

```python
from fastapi import FastAPI, Depends
from duar_auth import Duar

duar = Duar(
    base_url="http://localhost:9003",
    service_name="my-service",
    service_key="sk_...",
    mode="authz",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience="123-abc.apps.googleusercontent.com",  # your OAuth client_id
    actions=[
        {"action": "reports:export", "description": "Export reports"},
    ],
)

app = FastAPI(lifespan=duar.lifespan)
duar.protect(app)

# Tier 1: workspace role from JWT
@app.get("/projects")
async def list_projects(user=Depends(duar.require_user)):
    return await get_projects(user.workspace_id)

# Tier 2: RBAC action check
@app.get("/reports/export")
async def export(user=Depends(duar.require_action("reports:export"))):
    ...

# Tier 3: entity-level permission
@app.get("/projects/{id}")
async def get_project(id: str, auth=Depends(duar.get_auth)):
    if not await auth.can("project", id, "view"):
        raise HTTPException(403)
    ...
```

For DDD / Clean Architecture integration, see the [DDD guide](https://sidxz.github.io/duar/sdk/ddd/).

## JS/TS SDK

```bash
npm install @duar-auth/js @duar-auth/react
```

```tsx
import { IdpConfigs } from "@duar-auth/js";
import { AuthzProvider, AuthzGuard, useAuthz, useAuthzUser } from "@duar-auth/react";

function App() {
  return (
    <AuthzProvider
      config={{
        duarUrl: "http://localhost:9003",
        mintEndpoint: "/api/auth/mint", // YOUR backend route — holds the service key
        idps: { google: IdpConfigs.google("your-google-client-id") },
      }}
    >
      <AuthzGuard fallback={<Login />}>
        <Dashboard />
      </AuthzGuard>
    </AuthzProvider>
  );
}

function Login() {
  const { login } = useAuthz();
  return <button onClick={() => login("google")}>Sign in with Google</button>;
}

function Dashboard() {
  const user = useAuthzUser();
  return <p>{user.name} ({user.workspaceRole})</p>;
}
```

For Next.js, see `@duar-auth/nextjs` with Edge Middleware and server-side JWT verification.

## Project Structure

```
├── service/              # FastAPI microservice
├── sdk/                  # Python SDK (duar-auth)
├── sdks/                 # JS/TS SDKs (js, react, nextjs)
├── admin/                # React admin panel (Vite + TailwindCSS)
├── demo-authz/           # Demo app — AuthZ mode (Next.js + FastAPI)
├── demo-proxy/           # Demo app — Proxy mode (React + FastAPI)
├── pentest/              # Security testing suite
├── docs/                 # Documentation site (MkDocs Material)
├── docker-compose.yml    # Dev containers (PostgreSQL + Redis)
└── Makefile              # setup, start, admin, seed, lint, docs, pentest
```

## Security

Defense-in-depth middleware, per-endpoint rate limiting, and a comprehensive penetration testing suite. See the [security documentation](https://sidxz.github.io/duar/security/).

## Changelog

Notable changes — especially **breaking changes with before/after snippets for patching downstream apps** — are tracked in [`CHANGELOG.md`](./CHANGELOG.md).

## Contributing

See the [contributing guide](https://sidxz.github.io/duar/contributing/).

## License

MIT
