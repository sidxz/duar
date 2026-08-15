# React Integration

`@duar-auth/react` provides context providers, hooks, and components for React apps. This page covers authz mode (recommended). For proxy mode, the package also exports `DuarAuthProvider`, `useAuth`, `AuthGuard`, and `AuthCallback`.

```bash
npm install @duar-auth/js @duar-auth/react
```

## AuthzProvider

Wrap your app to provide auth context.

```tsx
import { AuthzProvider } from '@duar-auth/react'
import { IdpConfigs } from '@duar-auth/js'

function App() {
  return (
    <AuthzProvider config={{
      duarUrl: 'http://localhost:9003',
      mintEndpoint: '/api/auth/mint', // YOUR backend route — holds the service key
      idps: { google: IdpConfigs.google('your-google-client-id') },
    }}>
      <YourApp />
    </AuthzProvider>
  )
}
```

Pass a pre-created client via the `client` prop when you need the instance outside React.

Add the **`autoReauth`** prop (opt-in) for seamless persistence: when a reload leaves the session in `needs_reauth` (authz token survived, IdP token gone), the provider automatically runs `silentLogin()` (`prompt=none`) on mount. It has a built-in loop guard; if silent auth needs interaction, the callback falls back to interactive login.

```tsx
<AuthzProvider autoReauth config={{ /* … */ }}>
  <YourApp />
</AuthzProvider>
```

## useAuthz()

Full auth context. Throws if used outside `AuthzProvider`.

```tsx
const {
  user,             // DuarUser | null
  isAuthenticated,  // boolean (authz token AND IdP token present)
  authState,        // 'authenticated' | 'needs_reauth' | 'unauthenticated'
  needsReauth,      // boolean — authz token survived a reload but IdP token is gone
  isLoading,        // boolean
  login,            // (provider: string) => void
  silentLogin,      // (provider?: string) => boolean — prompt=none re-auth redirect
  consumeReturnTo,  // () => string | null — same-origin return path after re-auth
  resolve,          // (idpToken, provider) => Promise<AuthzResolveResponse>
  selectWorkspace,  // (idpToken, provider, wsId) => Promise<void>
  logout,           // () => void
  fetch,            // dual-header fetch
  fetchJson,        // <T>(input, init?) => Promise<T>
  client,           // DuarAuthz instance
} = useAuthz()
```

## Other hooks

**useAuthzUser()** -- returns `DuarUser`, throws if not authenticated.

```tsx
const user = useAuthzUser()
// { userId, email, name, workspaceId, workspaceSlug, workspaceRole, groups }
```

**useAuthzHasRole(minimum)** -- checks workspace role hierarchy (`viewer` < `editor` < `admin` < `owner`).

```tsx
const isAdmin = useAuthzHasRole('admin')
```

**useAuthzFetch()** -- shortcut to the dual-header fetch wrapper.

## AuthzGuard

Gate content behind authentication.

```tsx
<AuthzGuard fallback={<LoginPage />} loading={<Spinner />}>
  <Dashboard />
</AuthzGuard>
```

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `children` | `ReactNode` | required | Shown when authenticated |
| `fallback` | `ReactNode` | required | Shown when not authenticated |
| `loading` | `ReactNode` | `null` | Shown while checking auth state |

## AuthzCallback

Handles the OAuth callback. Reads `id_token` from the URL hash, resolves workspaces, auto-selects if one, shows picker if multiple.

```tsx
<AuthzCallback
  onSuccess={(user) => navigate('/dashboard')}
  onError={(err) => console.error(err)}
  workspaceSelector={({ workspaces, onSelect, isLoading }) => (
    <ul>
      {workspaces.map((ws) => (
        <li key={ws.id}>
          <button onClick={() => onSelect(ws.id)} disabled={isLoading}>
            {ws.name} ({ws.role})
          </button>
        </li>
      ))}
    </ul>
  )}
/>
```

| Prop | Type | Description |
|------|------|-------------|
| `onSuccess` | `(user: DuarUser, returnTo?: string \| null) => void` | Called after auth completes; `returnTo` is the same-origin path to restore after a silent re-auth |
| `onError` | `(error: Error) => void` | Called on error |
| `onSilentReauthFailed` | `(returnTo: string \| null) => void` | Called when a silent (`prompt=none`) re-auth needs interaction. The stale session is already cleared — trigger interactive `login()`. Falls through to `onError` if omitted |
| `loadingComponent` | `ReactNode` | Loading UI |
| `errorComponent` | `(error: Error) => ReactNode` | Error UI |
| `workspaceSelector` | `(props) => ReactNode` | Custom workspace picker |

## Complete example

```tsx
import { BrowserRouter, Routes, Route, useNavigate } from 'react-router-dom'
import { AuthzProvider, AuthzGuard, AuthzCallback, useAuthz, useAuthzUser } from '@duar-auth/react'
import { IdpConfigs } from '@duar-auth/js'

function App() {
  return (
    <AuthzProvider autoReauth config={{
      duarUrl: 'http://localhost:9003',
      idps: { google: IdpConfigs.google(import.meta.env.VITE_GOOGLE_CLIENT_ID) },
    }}>
      <BrowserRouter>
        <Routes>
          <Route path="/auth/callback" element={<Callback />} />
          <Route path="/*" element={
            <AuthzGuard fallback={<Login />} loading={<p>Loading...</p>}>
              <Dashboard />
            </AuthzGuard>
          } />
        </Routes>
      </BrowserRouter>
    </AuthzProvider>
  )
}

function Login() {
  const { login } = useAuthz()
  return <button onClick={() => login('google')}>Sign in with Google</button>
}

function Callback() {
  const navigate = useNavigate()
  const { login } = useAuthz()
  return (
    <AuthzCallback
      onSuccess={(_user, returnTo) => navigate(returnTo ?? '/', { replace: true })}
      onSilentReauthFailed={() => login('google')}
    />
  )
}

function Dashboard() {
  const user = useAuthzUser()
  const { logout } = useAuthz()
  return (
    <div>
      <p>Welcome, {user.name} ({user.workspaceRole})</p>
      <button onClick={logout}>Logout</button>
    </div>
  )
}
```
