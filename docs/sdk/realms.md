# Realms & M2M

When a service app is a [realm](../guide/realms.md) member, the SDK self-discovers the shared scope and transparently substitutes it — your application code does not change. The SDK also adds the no-user m2m primitives for [Flow B](../guide/realms.md#flow-b-no-user).

Everything here is **non-breaking for standalone services**: with no realm, `effective_scope == service_name`, and a pre-realm Duar (no `/realm` endpoint) degrades gracefully — the SDK stays standalone, it never crashes.

## Scope self-discovery

At startup (inside `duar.lifespan`) the SDK calls `GET /realm/whoami` and caches the result. Two read-only properties expose it:

```python
duar.effective_scope   # "acme-suite" for a member, else the service_name
duar.realm             # {"slug": "acme-suite", "name": "Acme Suite"} or None
```

You rarely call these directly — the `PermissionClient` and `RoleClient` owned by the `Duar` instance are automatically pointed at `effective_scope`, and `AuthzMiddleware` accepts an authz token whose `svc` is the realm slug (Flow A). To resolve scope manually (e.g. outside the lifespan):

```python
data = await duar.fetch_whoami()
# {"service_name": ..., "effective_scope": ..., "realm": {...} | None} or None on a pre-realm Duar
```

## Accepting an m2m token (receiver — Flow B)

`verify_m2m_token` verifies an inbound no-user token and returns a `SystemAuth`. Trust is rooted in Duar's RS256 signature plus `aud`/`type`/`svc` binding — never app-to-app trust.

```python
from duar_auth import SystemAuth   # exported from the package root

sys_auth: SystemAuth = duar.verify_m2m_token(token)
sys_auth.caller        # the realm member that minted it (server-stamped)
sys_auth.svc           # the realm slug
sys_auth.actions       # ["*"] = full in-realm trust in v1
sys_auth.can("reports:export")   # True if "*" in actions or the action is listed
```

`SystemAuth` is the no-user counterpart to `RequestAuth` — it carries service identity only, never a user. `verify_m2m_token` raises `DuarError` (`status_code` 401 for a bad/expired/wrong-type token, 403 for the wrong realm or wrong target).

### `require_system` dependency

Gate a system-only route with the `require_system` FastAPI dependency, which reads the m2m token from `Authorization: Bearer`:

```python
from fastapi import Depends
from duar_auth import SystemAuth

@app.post("/internal/reindex")
async def reindex(sys: SystemAuth = Depends(duar.require_system)):
    if not sys.can("search:reindex"):
        raise HTTPException(403)
    ...
```

!!! warning "Exclude m2m routes from the auth middleware"
    An m2m call carries **no IdP token**, so `AuthzMiddleware` would 401 it. Add the route's path to the middleware `exclude_paths` and gate it with `require_system` instead.

## Minting an m2m token (sender — Flow B)

`mint_m2m_token` mints (or returns a cached) token for an outbound system call. It caches the token and only re-mints once it passes ~80% of its TTL, so a tight background loop does not hammer Duar. Requires this service to be an active realm member (Duar rejects a standalone caller with 403).

```python
token = await duar.mint_m2m_token()
async with httpx.AsyncClient() as client:
    await client.post(
        "http://app-b.internal/internal/reindex",
        headers={"Authorization": f"Bearer {token}"},
    )
```

## End-to-end

| Side | Call | Used for |
|---|---|---|
| Sender | `await duar.mint_m2m_token()` | get a token for an outbound system call |
| Receiver | `duar.verify_m2m_token(token)` → `SystemAuth` | accept an inbound system call |
| Receiver (FastAPI) | `Depends(duar.require_system)` | gate a route to in-realm system callers |
| Either | `duar.effective_scope` / `duar.realm` | inspect the discovered scope |

For the equivalent JS calls (`M2mTokenClient`, `verifyM2mToken`, `fetchWhoami`) see [JS Server Utilities](../js-sdk/server.md). For the wire format see [API → Realms](../api/realms.md#m2m-token-claims).
