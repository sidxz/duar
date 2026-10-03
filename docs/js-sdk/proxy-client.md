# Proxy Client

`DuarAuth` is the browser auth client for proxy mode. Duar manages the full OAuth2 + PKCE redirect flow, token exchange, and refresh. You get a single access token + refresh token pair.

For the recommended authz mode, see [AuthZ Client](authz-client.md).

## Setup

```typescript
import { DuarAuth } from '@duar-auth/js'

const auth = new DuarAuth({
  duarUrl: 'http://localhost:9003',
  clientId: '00000000-0000-0000-0000-000000000000', // ClientApp id from admin panel
})
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `duarUrl` | `string` | required | Base URL of the Duar service |
| `clientId` | `string` | **required** | ClientApp UUID from the Duar admin panel. Binds this login flow to a specific registered app — Duar rejects a login whose `redirect_uri` does not belong to this `client_id`. Prevents cross-app auth-code interception. |
| `redirectUri` | `string` | `${origin}/auth/callback` | OAuth redirect URI. Must be listed on the ClientApp's registered `redirect_uris`. |
| `storage` | `TokenStore` | `MemoryStore` | Token storage backend |
| `autoRefresh` | `boolean` | `true` | Refresh tokens before expiry |
| `refreshBuffer` | `number` | `60` | Seconds before expiry to trigger refresh |

## How it works

```
1. auth.login('google')            -> PKCE + CSRF state, redirect to Duar
2. Duar -> Google -> callback  -> ?code=...&state=...
3. auth.verifyCallbackState()      -> verify CSRF state
4. auth.getWorkspaces(code)        -> list available workspaces
5. auth.selectWorkspace(code, id)  -> exchange code + PKCE verifier for tokens
6. Access + refresh tokens stored
```

## Methods

### login(provider)

Generate PKCE verifier and CSRF state, redirect to Duar's login endpoint.

```typescript
await auth.login('google')
```

### verifyCallbackState()

Verify the `state` parameter from the callback URL. Call before processing the auth code. Throws on mismatch.

### getWorkspaces(code)

Fetch available workspaces for the auth code.

```typescript
const workspaces = await auth.getWorkspaces(code)
// [{ id, name, slug, role }]
```

### selectWorkspace(code, workspaceId)

Complete token exchange with PKCE verifier. Stores access + refresh tokens.

```typescript
await auth.selectWorkspace(code, workspaceId)
```

### getToken() / getUser() / isAuthenticated

```typescript
const token = auth.getToken()        // raw access token string
const user = auth.getUser()          // DuarUser | null
if (auth.isAuthenticated) { /* */ }  // non-expired token exists
```

### fetch / fetchJson

Automatic `Authorization: Bearer` header. On 401, refreshes and retries.

```typescript
const res = await auth.fetch('/api/notes')
const notes = await auth.fetchJson<Note[]>('/api/notes')
```

### getProviders()

Fetch available OAuth providers from Duar.

```typescript
const providers = await auth.getProviders() // ['google', 'github']
```

### refresh() / logout() / onAuthStateChange(cb) / destroy()

```typescript
await auth.refresh()  // manual refresh (auto by default)
auth.logout()         // clear tokens, notify listeners
const unsub = auth.onAuthStateChange((user) => { /* */ })
auth.destroy()        // clean up timers
```

## Token storage

| Backend | Persistence |
|---------|-------------|
| `MemoryStore` (default) | Lost on page refresh |
| `LocalStorageStore` | Survives refresh, shared across tabs |
| `SessionStorageStore` | Cleared when tab closes |

```typescript
import { DuarAuth, LocalStorageStore } from '@duar-auth/js'
const auth = new DuarAuth({
  duarUrl: '...',
  clientId: '00000000-0000-0000-0000-000000000000',
  storage: new LocalStorageStore(),
})
```

## Complete example

```typescript
import { DuarAuth, LocalStorageStore } from '@duar-auth/js'

const auth = new DuarAuth({
  duarUrl: 'http://localhost:9003',
  clientId: '00000000-0000-0000-0000-000000000000',
  storage: new LocalStorageStore(),
})

// Login page
await auth.login('google')

// Callback page (/auth/callback)
const code = new URLSearchParams(window.location.search).get('code')
if (code) {
  auth.verifyCallbackState()
  const workspaces = await auth.getWorkspaces(code)
  if (workspaces.length === 1) {
    await auth.selectWorkspace(code, workspaces[0].id)
    window.location.href = '/dashboard'
  }
}

// Authenticated requests
const notes = await auth.fetchJson<Note[]>('/api/notes')
```

## How it differs from AuthZ mode

| | Proxy (`DuarAuth`) | AuthZ (`DuarAuthz`) |
|---|---|---|
| OAuth flow | Duar proxies (PKCE, redirect) | You configure IdPs, SDK redirects directly |
| Token exchange | code + PKCE verifier via `/auth/token` | IdP token via `/authz/resolve` |
| Tokens | Access + refresh (single JWT) | IdP token + authz token (dual) |
| Headers | `Authorization: Bearer` | `Authorization` + `X-Authz-Token` |
| Refresh | Uses refresh token | Re-resolves IdP token |
