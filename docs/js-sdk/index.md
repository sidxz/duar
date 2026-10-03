# JavaScript / TypeScript SDK

Three npm packages for browser auth, React bindings, and Next.js integration.

## Packages

```bash
# Core — browser client + server utilities
npm install @duar-auth/js

# React — provider, hooks, components
npm install @duar-auth/react

# Next.js — Edge Middleware, server helpers, client re-exports
npm install @duar-auth/nextjs
```

## Which package do I need?

| I'm building...                    | Install                                      |
|------------------------------------|----------------------------------------------|
| React SPA (Vite, CRA)             | `@duar-auth/js` + `@duar-auth/react` |
| Next.js app                        | `@duar-auth/js` + `@duar-auth/nextjs` |
| Node.js / Express API              | `@duar-auth/js` (server entry point)     |
| Vanilla JS / any framework         | `@duar-auth/js`                          |

## Two modes

**AuthZ mode** (recommended) -- your app handles IdP sign-in directly (Google, EntraID). Duar issues short-lived authorization tokens. Uses `DuarAuthz`, `AuthzProvider`.

**Proxy mode** -- Duar manages the full OAuth2 + PKCE redirect flow. Uses `DuarAuth`, `DuarAuthProvider`.

## Quick start (AuthZ mode)

```tsx
import { DuarAuthz, IdpConfigs } from '@duar-auth/js'

const authz = new DuarAuthz({
  duarUrl: 'http://localhost:9003',
  mintEndpoint: '/api/auth/mint', // YOUR backend route — holds the service key
  idps: { google: IdpConfigs.google('your-google-client-id') },
})

// Login redirects to Google
authz.login('google')

// After callback, resolve + select workspace
const result = await authz.resolve(idpToken, 'google')
await authz.selectWorkspace(idpToken, 'google', result.workspaces![0].id)

// Authenticated fetch with dual-token headers
const notes = await authz.fetchJson<Note[]>('/api/notes')
```

## Sections

- [AuthZ Client](authz-client.md) -- browser auth for authz mode (recommended)
- [Proxy Client](proxy-client.md) -- browser auth for proxy mode
- [React Integration](react.md) -- provider, hooks, guard, callback
- [Next.js Integration](nextjs.md) -- Edge Middleware + server helpers
- [Server Utilities](server.md) -- JWT verification, permissions, RBAC
