---
title: Duar
description: Authentication proxy and authorization microservice for your applications
---

![Duar](assets/images/splash.png)

# Duar

An authentication proxy and authorization microservice. Duar handles OAuth2/OIDC authentication from external IdPs, multi-tenant workspace management, and fine-grained Zanzibar-style permissions so you can focus on your application logic.

Built with **FastAPI**, **SQLAlchemy 2.0** (async), **PostgreSQL 16**, **Redis 7**, and **Authlib**.

---

<div class="grid cards" markdown>

-   :material-shield-lock:{ .lg .middle } **AuthZ Mode (Recommended)**

    ---

    Your app handles IdP login directly (Google, GitHub, EntraID). Duar validates the IdP token and issues an authorization JWT. Dual-token design with `idp_sub` binding.

    [:octicons-arrow-right-24: How it works](guide/how-it-works.md)

-   :material-office-building:{ .lg .middle } **Multi-Tenant Workspaces**

    ---

    Isolate users, groups, and resources by workspace. Role-based access at the workspace level with `owner`, `admin`, `editor`, and `viewer` roles embedded in every JWT.

    [:octicons-arrow-right-24: Workspaces](guide/workspaces.md)

-   :material-lock-check:{ .lg .middle } **Zanzibar-Style Permissions**

    ---

    Generic resource permissions with `service_name`, `resource_type`, and `resource_id`. Check access, list accessible resources, and share via ACLs.

    [:octicons-arrow-right-24: Permissions](guide/permissions.md)

-   :material-account-group:{ .lg .middle } **Custom RBAC**

    ---

    Define service actions (`notes:export`, `reports:generate`), create roles, assign to users. Check permissions at runtime with a single dependency.

    [:octicons-arrow-right-24: Roles](guide/roles.md)

-   :material-language-python:{ .lg .middle } **Python SDK**

    ---

    `pip install duar-auth` and integrate in minutes. Middleware, FastAPI dependencies, permission and role clients with a typed async API.

    [:octicons-arrow-right-24: Python SDK](sdk/index.md)

-   :material-language-typescript:{ .lg .middle } **JS / TS SDK**

    ---

    Three packages for browser, React, and Next.js. Token management, auth-aware fetch, React hooks, Edge Middleware, and server-side JWT verification.

    [:octicons-arrow-right-24: JS/TS SDK](js-sdk/index.md)

</div>

---

## Quick integration

```python
from duar_auth import Duar

duar = Duar(
    base_url="http://localhost:9003",
    service_name="my-app",
    service_key="sk_...",
    mode="authz",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience="123-abc.apps.googleusercontent.com",  # your OAuth client_id
)

app = FastAPI(lifespan=duar.lifespan)
duar.protect(app)

@app.get("/projects")
async def list_projects(user=Depends(duar.require_user)):
    return await get_projects(user.workspace_id)
```

---

## Get started

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Quickstart**

    ---

    Run Duar, configure an IdP, and connect your first app in 5 minutes.

    [:octicons-arrow-right-24: Quickstart](getting-started/quickstart.md)

-   :material-book-open-variant:{ .lg .middle } **Tutorials**

    ---

    Build a Team Notes app with all three authorization tiers.

    [:octicons-arrow-right-24: React + FastAPI](tutorial/react.md) | [:octicons-arrow-right-24: Next.js](tutorial/nextjs.md)

</div>
