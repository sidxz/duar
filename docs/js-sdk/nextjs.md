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

What it does: strips spoofed `x-duar-*` headers, verifies IdP token (signature + `aud` + optional `iss`) against IdP JWKS, verifies authz token against Duar JWKS, checks `idp_sub` binding, checks `svc` binding, sets `x-duar-*` headers for downstream components. API routes get 401 JSON; page routes redirect.

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
