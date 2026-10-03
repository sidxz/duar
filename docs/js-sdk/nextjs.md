# Next.js Integration

`@duar-auth/nextjs` provides Edge Middleware for JWT validation and server helpers for Server Components and Route Handlers.

```bash
npm install @duar-auth/js @duar-auth/nextjs
```

## AuthZ Middleware

Validates dual tokens (IdP + Duar authz) at the edge.

```typescript
// middleware.ts
import { createDuarAuthzMiddleware } from '@duar-auth/nextjs/authz-middleware'

export default createDuarAuthzMiddleware({
  duarUrl: process.env.DUAR_URL!,
  idpJwksUrl: 'https://www.googleapis.com/oauth2/v3/certs',
  idpAudience: process.env.GOOGLE_CLIENT_ID!,
  idpIssuer: 'https://accounts.google.com',
  serviceName: 'my-app',
  publicPaths: ['/login', '/auth/callback'],
})
export const config = { matcher: ['/((?!_next|favicon.ico).*)'] }
```

> **Next.js 16:** name the file `proxy.ts` (`middleware.ts` still works there but is
> deprecated); the default export works unchanged. This is unrelated to
> `@duar-auth/nextjs/proxy`, the route-handler reverse proxy. Whatever the file is called, it
> must exist: without it nothing strips client-sent `x-duar-*` headers.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `duarUrl` | `string` | required | Duar URL (derives JWKS endpoint) |
| `idpJwksUrl` | `string` | required | IdP JWKS URL for token verification |
| `idpAudience` | `string \| string[]` | **required** | Your app's OAuth client_id. Rejects tokens minted for any other client of the same IdP. |
| `idpIssuer` | `string` | `undefined` | Expected IdP `iss` claim. Strongly recommended. |
| `serviceName` | `string` | **required** | Your service's name (as registered in Duar). Authz token's `svc` claim must equal this — stops cross-service token replay. |
| `effectiveScope` | `string` | `undefined` | Realm slug (this service's shared scope). When set, the authz token's `svc` may equal either `serviceName` or this — so a [realm](../guide/realms.md) member accepts a realm-shared user token (Flow A). Resolve it once at startup with `fetchWhoami` from `@duar-auth/js/server`. Omit for standalone apps. |
| `publicPaths` | `string[]` | `[]` | Paths that skip auth |
| `loginPath` | `string` | `"/login"` | Redirect for unauthenticated page requests |
| `autoResolve` | `boolean` | `false` | Mint the authz token server-side when a request carries only the IdP token plus `X-Workspace-Id` (scripts, Postman, Swagger). Requires `serviceKey` and `idpProvider`. |
| `serviceKey` | `string` | `undefined` | Service key used for `POST /authz/resolve`. Server-only env — never `NEXT_PUBLIC_`. |
| `idpProvider` | `string` | `undefined` | Provider Duar validates the IdP token as: `'google'` or `'entra_id'`. |

What it does: strips spoofed `x-duar-*` headers, verifies IdP token (signature + `aud` + optional `iss`) against IdP JWKS, verifies authz token against Duar JWKS, checks `idp_sub` binding, checks `svc` binding, sets `x-duar-*` headers for downstream components. API routes get 401 JSON; page routes redirect.

### Calling the API from scripts (auto-resolve)

```ts
export default createDuarAuthzMiddleware({
  ...,
  autoResolve: true,
  serviceKey: process.env.DUAR_SERVICE_KEY!,
  idpProvider: 'google',
})
```

```bash
curl https://app.example.com/api/items \
  -H "Authorization: Bearer $ID_TOKEN" \
  -H "X-Workspace-Id: 5e60ba90-4b3e-4b1a-9dcb-9d76b1a1e3a1"
```

The IdP token is verified first, then the middleware mints through `POST /authz/resolve`, caches per `(idp_sub, workspace)` for 80% of the token TTL, de-duplicates concurrent first requests, and forwards the minted token as `x-authz-token` to your route handlers. `X-Authz-Token` wins when both headers are sent. Responses: missing both headers → `401` (detail names the headers); non-UUID workspace → `400`; Duar rejects the IdP token → `401` (Duar's reason appended); not a member → `403`; Duar rate limit → `429` with `Retry-After`; Duar rejects this app's service key, is unreachable, or returns an unusable body → `503`. Every auto-resolve response is JSON whether the path is an API or page route — a caller sending a Bearer token plus `X-Workspace-Id` is an API client by construction; page navigations without a Bearer token still redirect to `loginPath`.

## Proxy Middleware

For Duar's redirect-based OAuth flow. Validates a single JWT.

```typescript
// middleware.ts
import { createDuarMiddleware } from '@duar-auth/nextjs/middleware'

export default createDuarMiddleware({
  jwksUrl: process.env.DUAR_JWKS_URL!,
  publicPaths: ['/login', '/auth/callback'],
})
export const config = { matcher: ['/((?!_next|favicon.ico).*)'] }
```

Additional options: `audience` (default `"duar:access"`), `allowedWorkspaces` (optional workspace ID allowlist). Reads token from `Authorization: Bearer` header or `duar_access_token` cookie.

## Headers set by middleware

Both variants set these on success, readable in Server Components and Route Handlers:

| Header | Value |
|--------|-------|
| `x-duar-user-id` | User ID |
| `x-duar-email` | Email (percent-encoded) |
| `x-duar-name` | Display name (percent-encoded) |
| `x-duar-workspace-id` | Workspace ID |
| `x-duar-workspace-slug` | Workspace slug |
| `x-duar-workspace-role` | Workspace role |
| `x-duar-idp-sub` | IdP subject the authz token is bound to (authz middleware only) |
| `x-duar-actions` | Comma-separated RBAC actions (authz middleware only) |
| `x-duar-org-id` | The user's organization ID, from their email domain (omitted when the user has no org) |
| `x-duar-org-slug` | The user's organization slug (omitted when the user has no org) |
| `x-duar-org-public` | `true` if the user is in the public org; `false` otherwise, including no org |
| `x-authz-token` | The Duar authz token — on the auto-resolve path the middleware sets it so route handlers see the minted token |

> **These headers are only trustworthy behind the middleware.** It deletes every client-sent
> `x-duar-*` header on every path, then sets verified values. On a route its `matcher`
> excludes, or if the middleware is skipped, `getUser()`, `requireUser()` and `withAuth()`
> read whatever the client sent. Keep the `matcher` covering every route that calls them,
> and keep Next.js on the latest patch of its release line: middleware bypasses recur
> (CVE-2025-29927, then CVE-2026-44573, -44574, -44575, -45109 and -64642). The peer range
> `^14.2.25 || ^15.5.18 || ^16.2.11` excludes the versions with known App Router bypasses,
> but it is only a floor: npm refuses a conflicting install, while pnpm, yarn and
> `--legacy-peer-deps` only warn, so check `npm ls next`. 14.x is end-of-life and still has
> CVE-2026-44573 (Pages Router with `i18n`); Pages Router code that reads `x-duar-*`
> itself should move to 15.5.18+.

> **Org and action headers.** `x-duar-org-*` describe the user, not the workspace: one
> workspace can hold members of several orgs, so key data and authorization on
> `x-duar-workspace-id`, never the org, and test `x-duar-org-id` (not
> `x-duar-org-public: false`) for org membership. `x-duar-actions` is copied from the authz
> token, so a revoked role stays listed until the token is re-minted
> (`AUTHZ_TOKEN_EXPIRE_MINUTES`, default 5). When a check must see a revocation immediately,
> call [`RoleClient.checkAction()`](server.md#roleclient).

> **Prefer `getUser()` over reading these directly.** `x-duar-email` and
> `x-duar-name` are percent-encoded on the wire (HTTP header values are
> Latin-1, so a display name like `中文` or `Zoë` would otherwise throw). `getUser()`
> decodes them for you; if you read the raw headers, `decodeURIComponent()` them.

## Server helpers

```typescript
import { getUser, requireUser, getToken, withAuth } from '@duar-auth/nextjs/server'
```

**getUser()** -- returns `DuarUser | null` from middleware headers.

```tsx
// app/dashboard/page.tsx (Server Component)
import { getUser } from '@duar-auth/nextjs/server'

export default async function DashboardPage() {
  const user = await getUser()
  if (!user) return <p>Not authenticated</p>
  return <p>Welcome, {user.name}!</p>
}
```

**requireUser()** -- returns `DuarUser` or throws.

**getToken()** -- raw JWT string from Authorization header.

**withAuth(handler)** -- HOC for Route Handlers.

```typescript
// app/api/notes/route.ts
import { withAuth } from '@duar-auth/nextjs/server'

export const GET = withAuth(async (req, user) => {
  return Response.json({ workspace: user.workspaceId })
})
```

## Client components

The default import re-exports all React components with `'use client'`:

```tsx
'use client'
import { AuthzProvider, useAuthz, AuthzGuard, AuthzCallback } from '@duar-auth/nextjs'
```

See [React Integration](react.md) for hook and component details.

## Complete example

```typescript
// middleware.ts
import { createDuarAuthzMiddleware } from '@duar-auth/nextjs/authz-middleware'
export default createDuarAuthzMiddleware({
  duarUrl: process.env.DUAR_URL!,
  idpJwksUrl: 'https://www.googleapis.com/oauth2/v3/certs',
  idpAudience: process.env.GOOGLE_CLIENT_ID!,
  idpIssuer: 'https://accounts.google.com',
  serviceName: 'my-app',
  publicPaths: ['/login', '/auth/callback'],
})
export const config = { matcher: ['/((?!_next|favicon.ico).*)'] }
```

```tsx
// app/login/page.tsx
'use client'
import { AuthzProvider, useAuthz } from '@duar-auth/nextjs'
import { IdpConfigs } from '@duar-auth/js'

function LoginButton() {
  const { login } = useAuthz()
  return <button onClick={() => login('google')}>Sign in with Google</button>
}

export default function LoginPage() {
  return (
    <AuthzProvider config={{
      duarUrl: process.env.NEXT_PUBLIC_DUAR_URL!,
      idps: { google: IdpConfigs.google(process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID!) },
    }}>
      <LoginButton />
    </AuthzProvider>
  )
}
```

```tsx
// app/auth/callback/page.tsx
'use client'
import { AuthzProvider, AuthzCallback } from '@duar-auth/nextjs'
import { useRouter } from 'next/navigation'

export default function CallbackPage() {
  const router = useRouter()
  return (
    <AuthzProvider config={{ duarUrl: process.env.NEXT_PUBLIC_DUAR_URL! }}>
      <AuthzCallback onSuccess={() => router.push('/dashboard')} />
    </AuthzProvider>
  )
}
```

```tsx
// app/dashboard/page.tsx (Server Component)
import { getUser } from '@duar-auth/nextjs/server'
export default async function DashboardPage() {
  const user = await getUser()
  return <h1>Welcome, {user?.name}</h1>
}
```
