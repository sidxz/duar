# Duar Class

The `Duar` class is the recommended entry point. It wires middleware, creates clients, registers RBAC actions on startup, and cleans up on shutdown.

```python
from duar_auth import Duar

duar = Duar(
    base_url="http://localhost:9003",
    service_name="my-service",
    service_key="sk_...",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience="123-abc.apps.googleusercontent.com",  # your OAuth client_id
    idp_issuer="https://accounts.google.com",
)
```

## Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_url` | `str` | required | Root URL of the Duar service |
| `service_name` | `str` | required | Service name registered in Duar admin |
| `service_key` | `str` | required | Service API key from admin panel |
| `mode` | `str` | `"authz"` | `"authz"` or `"proxy"` |
| `idp_public_key` | `str \| None` | `None` | PEM public key for IdP token validation |
| `idp_jwks_url` | `str \| None` | `None` | JWKS endpoint for IdP token validation (preferred -- handles key rotation) |
| `idp_audience` | `str \| list[str] \| None` | `None` | **Required in authz mode.** The IdP `aud` claim this app expects -- typically your OAuth client_id. Without this check, any token signed by the IdP for any OAuth client would authenticate. |
| `idp_issuer` | `str \| None` | `None` | Expected IdP `iss` claim, e.g. `"https://accounts.google.com"`. Strongly recommended. |
| `actions` | `list[dict] \| None` | `None` | RBAC actions to register on startup |
| `allowed_workspaces` | `set[str] \| None` | `None` | Workspace IDs permitted to access this service. `None` allows all. Proxy mode only. |
| `cache_ttl` | `float` | `0` | Seconds to cache `accessible()` and `can()` results in the `PermissionClient`. `0` disables caching. Recommended: `30`–`120` for apps where permission changes are infrequent. Write operations (share, unshare, visibility changes) automatically invalidate the cache. |

In authz mode, both `idp_audience` and one of `idp_public_key` / `idp_jwks_url` are required.

## AuthZ Mode (Default)

Your app authenticates users directly with the IdP. Duar issues an authorization-only JWT. The middleware validates both tokens on each request.

```python
duar = Duar(
    base_url="https://duar.example.com",
    service_name="my-service",
    service_key="sk_...",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience="123-abc.apps.googleusercontent.com",
    idp_issuer="https://accounts.google.com",
    actions=[
        {"action": "reports:export", "description": "Export reports"},
        {"action": "reports:delete"},
    ],
)

app = FastAPI(lifespan=duar.lifespan)
duar.protect(app)
```

Requests must send two tokens:

- `Authorization: Bearer <idp_token>`
- `X-Authz-Token: <duar_authz_token>`

The middleware enforces:

- IdP token signature, `aud` (= `idp_audience`), and optional `iss`
- Authz token signature and `aud == "sentinel:authz"`
- `authz_token.idp_sub == idp_token.sub` (token binding)
- `authz_token.svc == service_name` (prevents cross-service token replay)

## Proxy Mode

Duar handles the entire OAuth flow and issues a single JWT with both identity and authorization claims.

```python
duar = Duar(
    base_url="https://duar.example.com",
    service_name="my-service",
    service_key="sk_...",
    mode="proxy",
    allowed_workspaces={"uuid-1", "uuid-2"},  # optional
)

app = FastAPI(lifespan=duar.lifespan)
duar.protect(app)
```

Requests send one token: `Authorization: Bearer <duar_jwt>`.

## `protect(app, exclude_paths=None)`

Adds authentication middleware to the FastAPI app.

- **AuthZ mode**: adds `AuthzMiddleware` (dual-token validation)
- **Proxy mode**: adds `JWTAuthMiddleware` (single JWT validation)

```python
duar.protect(app, exclude_paths=["/health", "/docs", "/openapi.json", "/webhooks"])
```

Default excluded paths: `["/health", "/docs", "/openapi.json"]`.

Can be called at module level before the lifespan runs -- in authz mode, the middleware reads keys lazily from the Duar instance.

## Lifespan

`duar.lifespan` is an async context manager factory for `FastAPI(lifespan=...)`.

**On startup:**

- AuthZ mode: fetches Duar's public key from its JWKS endpoint
- Registers RBAC actions if `actions` was provided

**On shutdown:**

- Closes all HTTP clients (`PermissionClient`, `RoleClient`, `AuthzClient`)

```python
app = FastAPI(lifespan=duar.lifespan)
```

## Properties

### `duar.permissions` -> `PermissionClient`

Lazily-created client for entity-level ACL operations. See [PermissionClient](permissions.md).

```python
allowed = await duar.permissions.can(token, "document", doc_id, "view")
```

### `duar.roles` -> `RoleClient`

Lazily-created client for RBAC operations. See [RoleClient](roles.md).

```python
allowed = await duar.roles.check_action(token, "reports:export", workspace_id)
```

### `duar.authz` -> `AuthzClient`

Lazily-created client for the authz token exchange endpoint.

## Dependencies

### `duar.require_user`

FastAPI dependency returning `AuthenticatedUser`. Raises 401 if not authenticated.

```python
@app.get("/me")
async def me(user: AuthenticatedUser = Depends(duar.require_user)):
    return {"email": user.email}
```

### `duar.get_auth`

FastAPI dependency returning `RequestAuth` -- a per-request context bundling the user, token, and wired-in clients. Useful for passing auth context into service/domain layers.

```python
@app.post("/documents")
async def create(body: CreateDoc, auth: RequestAuth = Depends(duar.get_auth)):
    await auth.register_resource("document", doc_id)
    if await auth.can("document", other_id, "view"):
        ...
```

### `duar.require_action(action)`

Dependency factory enforcing an RBAC action. Returns `AuthenticatedUser` or raises 403.

```python
@app.get("/reports/export")
async def export(user: AuthenticatedUser = Depends(duar.require_action("reports:export"))):
    ...
```
