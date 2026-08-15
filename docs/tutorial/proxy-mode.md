# Tutorial: Proxy Mode

Build the same Team Notes app from the [React tutorial](react.md), but using proxy mode instead of authz mode.

**Key difference:** In proxy mode, Duar handles the entire OAuth flow. The frontend redirects to Duar, which authenticates with the IdP, issues a single JWT, and redirects back. No dual tokens. No IdP configuration on the client.

Use proxy mode when you want Duar to own the login flow end-to-end. Use [authz mode](react.md) (recommended) when you want the frontend to authenticate with the IdP directly and keep Duar as a pure authorization service.

## Prerequisites

Same as the [React tutorial](react.md#prerequisites), except:

- No Google Client ID needed on the frontend (Duar handles IdP config)
- Client app redirect URI: `http://localhost:5173/auth/callback`

## Backend Differences

### Config

The `Duar` class takes `mode="proxy"` and does not need `idp_jwks_url`.

```python
# config.py
from duar_auth import Duar

duar = Duar(
    base_url="http://localhost:9003",
    service_name="team-notes",
    service_key="sk_your_key_here",
    mode="proxy",
    actions=[
        {"action": "notes:export", "description": "Export notes as JSON"},
    ],
)
```

### Middleware

`duar.protect(app)` adds `JWTAuthMiddleware` (not `AuthzMiddleware`). It validates a single `Authorization: Bearer <duar_jwt>` token per request.

```python
# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import duar

app = FastAPI(title="Team Notes", lifespan=duar.lifespan)
duar.protect(app, exclude_paths=["/health", "/docs", "/openapi.json"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Routes

The routes are identical to the authz-mode tutorial. `get_current_user`, `require_role`, `duar.require_action`, and `duar.permissions.can` all work the same way. The middleware populates `request.state.user` regardless of mode.

One difference: `request.state.token` contains the Duar JWT (single token) instead of the authz token. The `get_token` helper works identically.

```python
# Same routes as react.md Steps 3 -- no changes needed
from duar_auth.dependencies import get_current_user, get_workspace_id, require_role
```

## Frontend Differences

### Auth Client

Use `DuarAuth` instead of `DuarAuthz`. No IdP configuration needed -- Duar handles it.

```typescript
// src/api/client.ts
import { DuarAuth } from "@duar-auth/js";

const DUAR_URL = import.meta.env.VITE_DUAR_URL || "http://localhost:9003";
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:9200";
const CLIENT_ID = import.meta.env.VITE_DUAR_CLIENT_ID;
if (!CLIENT_ID) {
  throw new Error("VITE_DUAR_CLIENT_ID is required — get it from the Duar admin panel");
}

export const duarClient = new DuarAuth({
  duarUrl: DUAR_URL,
  clientId: CLIENT_ID,
});

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  return duarClient.fetchJson<T>(`${BACKEND_URL}${path}`, options);
}
```

!!! tip "Why `clientId` is required"
    `clientId` is the ClientApp UUID you get from the Duar admin panel. Duar uses it to bind a login to a specific registered app — a crafted link cannot redirect the OAuth flow to another app's callback. See [Vuln 7](../security.md) for background.

`duarClient.fetchJson()` attaches a single `Authorization: Bearer <token>` header (no `X-Authz-Token`).

### Provider + App Shell

Use `DuarAuthProvider`, `AuthGuard`, and `AuthCallback` instead of the authz variants.

```tsx
// src/main.tsx
import { DuarAuthProvider } from "@duar-auth/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { duarClient } from "./api/client";

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DuarAuthProvider client={duarClient}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </DuarAuthProvider>
  </StrictMode>,
);
```

```tsx
// src/App.tsx
import { AuthGuard } from "@duar-auth/react";
import { Route, Routes } from "react-router-dom";
import { AuthCallback } from "./pages/AuthCallback";
import { Login } from "./pages/Login";
import { NoteList } from "./pages/NoteList";

export function App() {
  return (
    <Routes>
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route
        path="*"
        element={
          <AuthGuard fallback={<Login />} loading={<div>Loading...</div>}>
            <Routes>
              <Route path="/" element={<NoteList />} />
            </Routes>
          </AuthGuard>
        }
      />
    </Routes>
  );
}
```

### Login

`login("google")` redirects to Duar, which redirects to Google. No IdP client ID needed on the frontend.

```tsx
// src/pages/Login.tsx
import { useAuth } from "@duar-auth/react";

export function Login() {
  const { login } = useAuth();
  return <button onClick={() => login("google")}>Sign in with Google</button>;
}
```

### Callback

```tsx
// src/pages/AuthCallback.tsx
import { AuthCallback as DuarCallback } from "@duar-auth/react";
import { useNavigate } from "react-router-dom";

export function AuthCallback() {
  const navigate = useNavigate();
  return (
    <DuarCallback
      onSuccess={() => navigate("/notes", { replace: true })}
      workspaceSelector={({ workspaces, onSelect, isLoading }) => (
        <div>
          <h2>Select Workspace</h2>
          {workspaces.map((ws) => (
            <button key={ws.id} onClick={() => onSelect(ws.id)} disabled={isLoading}>
              {ws.name} ({ws.role})
            </button>
          ))}
        </div>
      )}
    />
  );
}
```

### Pages

Use `useUser` instead of `useAuthzUser`. Everything else is the same.

```tsx
// src/pages/NoteList.tsx
import { useUser } from "@duar-auth/react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export function NoteList() {
  const user = useUser();
  const canCreate = ["editor", "admin", "owner"].includes(user.workspaceRole);

  const { data: notes = [] } = useQuery({
    queryKey: ["notes"],
    queryFn: () => apiFetch<any[]>("/notes"),
  });

  return (
    <div>
      <h1>Notes</h1>
      {canCreate && <button>New Note</button>}
      <ul>
        {notes.map((note) => (
          <li key={note.id}>{note.title}</li>
        ))}
      </ul>
    </div>
  );
}
```

## Side-by-Side Comparison

| Aspect | AuthZ Mode | Proxy Mode |
|--------|-----------|------------|
| Who handles IdP login | Frontend (direct) | Duar (redirect) |
| Tokens per request | 2 (IdP + authz) | 1 (Duar JWT) |
| Frontend IdP config | Required (client ID, JWKS) | Not needed |
| JS client class | `DuarAuthz` | `DuarAuth` |
| React provider | `AuthzProvider` | `DuarAuthProvider` |
| Route guard | `AuthzGuard` | `AuthGuard` |
| User hook | `useAuthzUser` | `useUser` |
| Auth hook | `useAuthz` | `useAuth` |
| Callback component | `AuthzCallback` | `AuthCallback` |
| Backend middleware | `AuthzMiddleware` | `JWTAuthMiddleware` |
| Backend routes | Identical | Identical |
| Permission/Role checks | Identical | Identical |

The backend authorization logic (workspace roles, RBAC actions, entity ACLs) works identically in both modes. The difference is purely in how the user authenticates and how tokens are structured.
