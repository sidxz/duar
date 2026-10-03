# Python SDK

```bash
pip install duar-auth
```

Import as `duar_auth`.

## What It Provides

- **`Duar` class** -- one-liner setup: middleware, lifespan, clients, dependencies
- **`AuthzMiddleware`** -- dual-token validation for authz mode (IdP + Duar tokens)
- **`JWTAuthMiddleware`** -- single JWT validation for proxy mode
- **FastAPI dependencies** -- `get_current_user`, `require_role()`, `require_action()`, `get_auth`
- **`PermissionClient`** -- Zanzibar-style entity ACLs (check, register, share, accessible)
- **`RoleClient`** -- RBAC action registration and checks
- **`RequestAuth`** -- per-request auth context for DDD integration
- **`SystemAuth`** -- no-user (m2m) in-realm caller context for [Flow B](../guide/realms.md#flow-b-no-user)
- **Realm m2m** -- `mint_m2m_token()`, `verify_m2m_token()`, `require_system` ([Realms & M2M](realms.md))
- **Type definitions** -- `AuthenticatedUser`, `WorkspaceContext`, `DuarError`

## Minimal Example

```python
from fastapi import Depends, FastAPI
from duar_auth import Duar
from duar_auth.types import AuthenticatedUser

duar = Duar(
    base_url="http://localhost:9003",
    service_name="my-service",
    service_key="sk_...",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience="123-abc.apps.googleusercontent.com",  # your OAuth client_id
    idp_issuer="https://accounts.google.com",           # strongly recommended
)

app = FastAPI(lifespan=duar.lifespan)
duar.protect(app)

@app.get("/me")
async def me(user: AuthenticatedUser = Depends(duar.require_user)):
    return {"email": user.email, "role": user.workspace_role}
```

!!! info "`idp_audience` is required in authz mode"
    Without it, the middleware would accept Google-signed tokens minted for other OAuth clients. The value is your OAuth client_id as registered with the IdP.

## Requirements

| Detail       | Value              |
|--------------|--------------------|
| Python       | >= 3.12            |
| PyPI name    | `duar-auth` |
| Import name  | `duar_auth`    |

Key dependencies: `pyjwt[crypto]`, `httpx`, `starlette`, `fastapi`.

## Pages

- [Duar Class](duar-class.md) -- constructor, modes, lifespan, properties
- [Middleware](middleware.md) -- AuthzMiddleware and JWTAuthMiddleware
- [FastAPI Dependencies](dependencies.md) -- dependency injection helpers
- [PermissionClient](permissions.md) -- entity-level access control
- [RoleClient](roles.md) -- RBAC action checks
- [DDD / Clean Architecture](ddd.md) -- integration with layered architectures
