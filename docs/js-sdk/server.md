# Server Utilities

`@duar-auth/js/server` provides JWT verification, permission checks, and RBAC action checks for Node.js and Edge runtimes.

```typescript
import { verifyToken, payloadToUser, PermissionClient, RoleClient } from '@duar-auth/js/server'
```

## verifyToken

Verify a Duar JWT against a JWKS endpoint. Uses `jose` (Edge-compatible).

```typescript
const payload = await verifyToken(token, {
  jwksUrl: 'http://localhost:9003/.well-known/jwks.json',
})
const user = payloadToUser(payload)
// { userId, email, name, workspaceId, workspaceSlug, workspaceRole, groups }
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `jwksUrl` | `string` | required | Duar JWKS endpoint |
| `audience` | `string` | `"duar:access"` | Expected `aud` claim |
| `issuer` | `string` | -- | Expected `iss` claim |

JWKS keys are fetched and cached automatically.

## PermissionClient

Zanzibar-style permission checks. Mirrors the Python SDK.

```typescript
const permissions = new PermissionClient(
  'http://localhost:9003', 'my-service', 'sk_my_service_key',
)
```

!!! note "Which token?"
    The `token` argument on every `PermissionClient` and `RoleClient` method must be a **Duar-signed** token: the access token in proxy mode, or the authz token (the `X-Authz-Token` request header) in [AuthZ mode](authz-client.md). In AuthZ mode the `Authorization` header carries the IdP token — Duar can't decode it, so passing it fails every call with a 401.

**can(token, resourceType, resourceId, action)** -- single permission check.

```typescript
const allowed = await permissions.can(token, 'document', docId, 'view')
```

**check(token, checks)** -- batch check.

```typescript
const results = await permissions.check(token, [
  { service_name: 'my-service', resource_type: 'document', resource_id: docId, action: 'view' },
  { service_name: 'my-service', resource_type: 'document', resource_id: docId, action: 'edit' },
])
```

**registerResource(request)** -- register a resource (service key, no JWT needed).

```typescript
await permissions.registerResource({
  service_name: 'my-service', resource_type: 'document', resource_id: docId,
  workspace_id: workspaceId, owner_id: userId, visibility: 'workspace',
})
```

**share(token, resourceType, resourceId, share)** -- grant access.

```typescript
await permissions.share(token, 'document', docId, {
  grantee_type: 'user', grantee_id: targetUserId, permission: 'edit',
})
```

**accessible(token, resourceType, action, workspaceId, limit?)** -- list accessible resource IDs.

```typescript
const result = await permissions.accessible(token, 'document', 'view', workspaceId)
// { resource_ids: ['doc1', 'doc2'], has_full_access: false }
```

## RoleClient

RBAC action checks. Mirrors the Python SDK.

```typescript
const roles = new RoleClient(
  'http://localhost:9003', 'my-service', 'sk_my_service_key',
)
```

**registerActions(actions)** -- register at startup (service key).

```typescript
await roles.registerActions([
  { action: 'notes:create', description: 'Create notes' },
  { action: 'notes:export', description: 'Export notes' },
])
```

**checkAction(token, action, workspaceId)** -- check single action.

```typescript
const allowed = await roles.checkAction(token, 'notes:export', workspaceId)
```

**getUserActions(token, workspaceId)** -- list permitted actions.

```typescript
const actions = await roles.getUserActions(token, workspaceId)
// ['notes:create', 'notes:export']
```

## Express example

```typescript
import express from 'express'
import { verifyToken, payloadToUser, PermissionClient } from '@duar-auth/js/server'

const app = express()
const permissions = new PermissionClient('http://localhost:9003', 'my-service', process.env.SERVICE_KEY)

async function authenticate(req, res, next) {
  const token = req.headers.authorization?.replace('Bearer ', '')
  if (!token) return res.status(401).json({ error: 'Unauthorized' })
  try {
    req.user = payloadToUser(await verifyToken(token, {
      jwksUrl: 'http://localhost:9003/.well-known/jwks.json',
    }))
    req.token = token
    next()
  } catch {
    res.status(401).json({ error: 'Invalid token' })
  }
}

app.get('/api/documents/:id', authenticate, async (req, res) => {
  const allowed = await permissions.can(req.token, 'document', req.params.id, 'view')
  if (!allowed) return res.status(403).json({ error: 'Forbidden' })
  res.json(await getDocument(req.params.id))
})
```

## Realm m2m (server only)

For [realm](../guide/realms.md) members, `@duar-auth/js/server` adds the no-user m2m primitives for [Flow B](../guide/realms.md#flow-b-no-user). These are **server-entry only** — they hold the service key and must never reach a browser. (`@duar-auth/react` deliberately has no m2m surface.)

The examples below point at `http://duar-internal:9010` — the unpublished [internal listener](../deployment/index.md#network-split-public--internal-listeners), not the public `:9003` URL browser flows use.

```typescript
import { fetchWhoami, verifyM2mToken, M2mTokenClient } from '@duar-auth/js/server'
```

### fetchWhoami

Self-discover this service's shared scope (standalone → `effective_scope === service_name`, `realm: null`).

```typescript
const who = await fetchWhoami({ duarUrl: 'http://duar-internal:9010', serviceKey: process.env.SERVICE_KEY })
// { service_name, effective_scope, realm: { slug, name } | null }
```

### M2mTokenClient (mint — sender)

Mints and caches m2m tokens for outbound system calls; re-mints only past ~80% of the TTL.

```typescript
const m2m = new M2mTokenClient('http://duar-internal:9010', process.env.SERVICE_KEY)
const token = await m2m.getToken()
await fetch('http://app-b.internal/internal/reindex', {
  headers: { Authorization: `Bearer ${token}` },
})
```

### verifyM2mToken (accept — receiver)

Verifies an inbound m2m token and returns a `SystemAuth`. Throws on any failure (bad signature, wrong realm, wrong type, expired).

```typescript
const sys = await verifyM2mToken(token, {
  jwksUrl: 'http://duar-internal:9010/.well-known/jwks.json',
  effectiveScope: 'acme-suite',   // the token's svc must equal this
  serviceName: 'reports',         // optional — checked against aud_target when set
})
sys.caller            // minting member (server-stamped)
sys.svc               // realm slug
sys.actions           // string[] — granted actions (["*"] = full realm trust)
sys.can('search:reindex')   // true if actions includes "*" or the action
```

| Option | Type | Description |
|--------|------|-------------|
| `jwksUrl` | `string` | JWKS endpoint of the Duar that signs m2m tokens |
| `effectiveScope` | `string` | this service's realm slug; the token's `svc` must equal it |
| `serviceName` | `string?` | checked against the token's `aud_target` when set |
| `issuer` | `string?` | expected `iss` claim |

Next.js apps get the same three helpers re-exported from `@duar-auth/nextjs/server`. See [Realms](../guide/realms.md) for the trust model and [API → Realms](../api/realms.md) for the wire format.
