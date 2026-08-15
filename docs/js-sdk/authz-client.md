# AuthZ Client

`DuarAuthz` is the browser auth client for authz mode. It manages dual tokens: an IdP token (identity, from Google/EntraID) and a Duar authz token (authorization).

## Setup

```typescript
import { DuarAuthz, IdpConfigs } from '@duar-auth/js'

const authz = new DuarAuthz({
  duarUrl: 'http://localhost:9003',
  mintEndpoint: '/api/auth/mint', // YOUR backend route — must not be Duar
  idps: { google: IdpConfigs.google('your-google-client-id') },
})
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `duarUrl` | `string` | required | Base URL of the Duar service. Used only for **discovery** (listing workspaces for an IdP token). |
| `mintEndpoint` | `string` | **required** | URL of your backend's mint route. The browser calls here (not Duar directly) to exchange IdP token + workspace_id for an authz token. Your backend forwards to Duar's `/authz/resolve` with `X-Service-Key`. See [AuthZ Mode Security](../security.md#authz-mode-security). |
| `idps` | `Record<string, IdpConfig>` | `{}` | IdP configs keyed by provider name |
| `redirectUri` | `string` | `${origin}/auth/callback` | OAuth redirect URI |
| `storage` | `AuthzTokenStore` | `AuthzMemoryStore` | Token storage backend |
| `autoRefresh` | `boolean` | `true` | Refresh authz token before expiry |
| `refreshBuffer` | `number` | `30` | Seconds before expiry to trigger refresh |

### Backend mint route

The `mintEndpoint` must accept `{idp_token, provider, workspace_id, nonce?}` and return the same shape as Duar's `/authz/resolve`. FastAPI example:

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import uuid
from your_app.duar_instance import duar  # your Duar SDK instance

router = APIRouter()

class MintRequest(BaseModel):
    idp_token: str
    provider: str
    workspace_id: uuid.UUID
    nonce: str | None = None

@router.post("/api/auth/mint")
async def mint_authz_token(body: MintRequest):
    try:
        return await duar.authz.resolve(
            idp_token=body.idp_token,
            provider=body.provider,
            workspace_id=body.workspace_id,
            nonce=body.nonce,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

Add the route to `duar.protect(app, exclude_paths=[...])` — it's called before the user has an authz token.

Next.js Route Handler:

```typescript
// app/api/auth/mint/route.ts
import { NextResponse } from 'next/server'

export async function POST(req: Request) {
  const body = await req.json()
  const r = await fetch(`${process.env.DUAR_URL}/authz/resolve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Service-Key': process.env.DUAR_SERVICE_KEY!, // server-side only
    },
    body: JSON.stringify(body),
  })
  return NextResponse.json(await r.json(), { status: r.status })
}
```

Built-in IdP helpers: `IdpConfigs.google(clientId)`, `IdpConfigs.entraId(clientId, tenantId)`. Pass a custom `IdpConfig` object for other providers.

## How it works

```
1. authz.login('google')              -> redirect to Google
2. Google redirects back with #id_token=...
3. authz.handleCallback()             -> extract token, verify nonce
4. authz.resolve(idpToken, provider)  -> POST /authz/resolve, get workspaces
5. authz.selectWorkspace(...)         -> POST /authz/resolve with workspace_id
6. Both tokens stored, auto-refresh scheduled
```

## Methods

### login(provider)

Redirect to the IdP's authorization page. Provider must be configured in `idps`.

```typescript
authz.login('google')
```

### handleCallback()

Interpret the IdP response in the URL hash after a redirect. Verifies the nonce. Returns a discriminated result (or `null` if there is no IdP response in the URL):

```typescript
const cb = authz.handleCallback()
//  | { status: 'success', idpToken, provider, returnTo }   -> proceed to resolve/selectWorkspace
//  | { status: 'silent_failed', error, provider, returnTo } -> silentLogin() couldn't complete; fall back to login()
//  | null                                                    -> not a callback
```

Throws on a genuine OAuth error or a nonce mismatch (possible replay). `returnTo` is the same-origin path the user was on before a `silentLogin()` redirect (or `null`). Accepts an optional pre-captured hash, `handleCallback(hash)`, for React StrictMode.

### resolve(idpToken, provider)

Validate IdP token with Duar, discover workspaces.

```typescript
const result = await authz.resolve(idpToken, 'google')
// result.user       -> { id, email, name }
// result.workspaces -> [{ id, name, slug, role }]
```

### selectWorkspace(idpToken, provider, workspaceId)

Exchange IdP token for a Duar authz token scoped to a workspace. POSTs to the configured `mintEndpoint` on your backend (not Duar). Propagates `sessionStorage.duar_authz_nonce` automatically for replay protection.

```typescript
await authz.selectWorkspace(idpToken, 'google', 'ws-uuid')
```

### getAuthState() / getUser() / isAuthenticated / needsReauth

Auth state is derived from **both** tokens. A valid authz token alone is not enough — the memory-only IdP token (gone after a reload) is also required to authenticate a request. `getAuthState()` reports this honestly so the app never renders "logged in" while every request 401s:

```typescript
authz.getAuthState()
//  'authenticated'   -> authz token + IdP token present; requests work
//  'needs_reauth'    -> authz token survived (e.g. reload) but IdP token is gone
//  'unauthenticated' -> no usable authz token

const user = authz.getUser()       // non-null ONLY when authenticated
if (authz.isAuthenticated) { /* ... */ }   // === getAuthState() === 'authenticated'
if (authz.needsReauth) { authz.silentLogin() }  // re-auth after reload (see below)
```

### silentLogin(provider?) / consumeReturnTo()

Recover from `needs_reauth` (e.g. after a page reload) without a full manual login. `silentLogin()` does a top-level `prompt=none` redirect to the IdP using the stored provider; with a live IdP session it bounces straight back with a fresh `id_token` (usually no UI). Returns `false` if it no-ops (no provider known, or an attempt is already in flight — there is a built-in loop guard). A full-page redirect is used deliberately over a hidden iframe, which third-party-cookie rules make unreliable.

```typescript
if (authz.needsReauth) authz.silentLogin()        // → resolves on the callback
// on the callback, handleCallback() returns 'silent_failed' if interaction is needed
const returnTo = authz.consumeReturnTo()          // same-origin path to restore, or null
```

### getHeaders()

```typescript
authz.getHeaders()
// { Authorization: 'Bearer <idp_token>', 'X-Authz-Token': '<authz_token>' }
```

### fetch / fetchJson

Inject dual-token headers. On 401, refresh and retry once.

```typescript
const res = await authz.fetch('/api/notes')
const notes = await authz.fetchJson<Note[]>('/api/notes')
```

### onAuthStateChange(cb) / logout() / destroy()

```typescript
const unsub = authz.onAuthStateChange((user) => { /* ... */ })
authz.logout()   // clear tokens, notify listeners
authz.destroy()  // clean up timers
```

## Token storage

| Backend | Persistence |
|---------|-------------|
| `AuthzMemoryStore` (default) | Lost on page refresh |
| `AuthzLocalStorageStore` | Authz token + metadata persist; **IdP token stays in memory only** |

```typescript
import { DuarAuthz, AuthzLocalStorageStore } from '@duar-auth/js'
const authz = new DuarAuthz({
  duarUrl: '...', storage: new AuthzLocalStorageStore(),
})
```

!!! info "IdP token is not persisted"
    `AuthzLocalStorageStore` deliberately keeps the IdP token (which is long-lived and trust-critical — a Google ID token lasts ~1h and authenticates on every request) **in instance memory only**. It does not survive a page reload. This reduces the blast radius of XSS: an attacker who reads `localStorage` does not get the IdP token, only the short-lived (~5 min) authz token.

    Trade-off: after a page reload the SDK has no IdP token. `getAuthState()` then reports `needs_reauth` (and `isAuthenticated` is `false`, `getUser()` is `null`) — so the app shows login instead of a broken "zombie" page that 401s on every request. Call `silentLogin()` to re-auth seamlessly via the IdP's existing session, or front your frontend with a backend route that sets an `HttpOnly` cookie holding the tokens server-side for true persistent sessions.

## `handleCallback()` nonce enforcement

`handleCallback()` requires that `duar_authz_nonce` exists in `sessionStorage`. If it doesn't (e.g. the callback was opened in a new tab that did not initiate the login), the SDK throws:

```
Error: No login flow in progress — callback rejected. Start login from this tab.
```

This prevents a login-CSRF where an attacker links a victim to `.../auth/callback#id_token=<attacker_token>` and silently establishes the attacker's identity in the victim's app.

## Complete example

```typescript
import { DuarAuthz, IdpConfigs, AuthzLocalStorageStore } from '@duar-auth/js'

const authz = new DuarAuthz({
  duarUrl: 'http://localhost:9003',
  mintEndpoint: '/api/auth/mint',
  idps: { google: IdpConfigs.google('your-client-id') },
  storage: new AuthzLocalStorageStore(),
})

// Login page
authz.login('google')

// Callback page (/auth/callback)
const cb = authz.handleCallback()
if (cb?.status === 'success') {
  const result = await authz.resolve(cb.idpToken, cb.provider)
  if (result.workspaces?.length === 1) {
    await authz.selectWorkspace(cb.idpToken, cb.provider, result.workspaces[0].id)
    window.location.href = cb.returnTo ?? '/dashboard'
  }
} else if (cb?.status === 'silent_failed') {
  authz.login('google') // silent re-auth needs interaction → fall back to interactive
}

// Protected page boot: recover a reloaded session seamlessly
if (authz.needsReauth) authz.silentLogin()

// After auth
const notes = await authz.fetchJson<Note[]>('/api/notes')
```

## AuthZ vs Proxy mode

| | AuthZ (`DuarAuthz`) | Proxy (`DuarAuth`) |
|---|---|---|
| IdP interaction | You configure IdPs, SDK redirects | Duar manages redirect flow |
| Tokens stored | IdP token + authz token | Access + refresh token |
| Headers sent | `Authorization` + `X-Authz-Token` | `Authorization` only |
| PKCE | Not needed (implicit flow) | Generated by SDK |
