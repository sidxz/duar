# Tutorial: React + FastAPI

Build a Team Notes app with FastAPI and React that uses all three Duar authorization tiers: workspace roles, custom RBAC, and entity ACLs.

**What you'll build:** A note-taking API where authenticated users list notes (Tier 1), editors create notes (Tier 1), users with a custom role export notes (Tier 2), and per-note view/edit permissions are checked at the entity level (Tier 3).

**Mode:** AuthZ (recommended). The frontend authenticates with the IdP directly. Duar issues an authorization token. Both tokens travel on every request.

## Prerequisites

- Duar running on `:9003` ([Getting Started](../getting-started/index.md))
- A service app created in the admin panel (you need the `sk_...` key)
- A client app registered with redirect URI `http://localhost:5173/auth/callback`
- Google OAuth client ID
- Python 3.12+, Node.js 18+

## Step 1: Backend Setup

```bash
mkdir -p team-notes/backend && cd team-notes/backend
uv init && uv add fastapi uvicorn pydantic-settings duar-auth
```

The `Duar` class is the single entry point -- it creates middleware, permission/role clients, and a lifespan handler.

```python
# backend/config.py
from duar_auth import Duar

duar = Duar(
    base_url="http://localhost:9003",
    service_name="team-notes",
    service_key="sk_your_key_here",
    mode="authz",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience="123-abc.apps.googleusercontent.com",  # your Google OAuth client_id
    idp_issuer="https://accounts.google.com",
    actions=[
        {"action": "notes:export", "description": "Export notes as JSON"},
    ],
)
```

!!! warning "`idp_audience` is required"
    Without it the middleware would accept any Google-signed token from any OAuth client — including one minted by an attacker for their own Google app. The value must equal your frontend's Google OAuth client_id.

```python
# backend/main.py
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

`duar.protect(app)` adds `AuthzMiddleware`, which validates two tokens per request:

- `Authorization: Bearer <idp_token>` -- proves identity
- `X-Authz-Token: <authz_token>` -- proves authorization (issued by Duar)

After validation, `request.state.user` is an `AuthenticatedUser` with `user_id`, `email`, `workspace_id`, `workspace_role`, etc.

## Step 2: Note Model

In-memory storage for simplicity:

```python
# backend/models.py
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

@dataclass
class Note:
    id: uuid.UUID
    title: str
    content: str
    workspace_id: uuid.UUID
    owner_id: uuid.UUID
    owner_name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

_notes: dict[uuid.UUID, Note] = {}

def list_by_workspace(wid): return [n for n in _notes.values() if n.workspace_id == wid]
def get(nid):               return _notes.get(nid)
def create(**kw):            n = Note(id=uuid.uuid4(), **kw); _notes[n.id] = n; return n
```

## Step 3: Routes -- All Three Tiers

```python
# backend/routes.py
import uuid
from dataclasses import asdict
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from duar_auth.dependencies import get_current_user, get_workspace_id, require_role
from duar_auth.types import AuthenticatedUser
from config import duar
import models

router = APIRouter()

class CreateNoteRequest(BaseModel):
    title: str
    content: str

def get_token(request: Request) -> str:
    return request.state.token
```

**Tier 1 -- List notes** (any authenticated user):

```python
@router.get("/notes")
async def list_notes(workspace_id: uuid.UUID = Depends(get_workspace_id)):
    return [asdict(n) for n in models.list_by_workspace(workspace_id)]
```

**Tier 1 + 3 -- Create note** (editor+) and register for entity ACLs:

```python
@router.post("/notes", status_code=201)
async def create_note(
    body: CreateNoteRequest,
    user: AuthenticatedUser = Depends(require_role("editor")),
):
    note = models.create(
        title=body.title, content=body.content,
        workspace_id=user.workspace_id,
        owner_id=user.user_id, owner_name=user.name,
    )
    await duar.permissions.register_resource(
        resource_type="note", resource_id=note.id,
        workspace_id=user.workspace_id, owner_id=user.user_id,
        visibility="workspace",
    )
    return asdict(note)
```

`require_role("editor")` enforces the hierarchy: `viewer < editor < admin < owner`.

**Tier 2 -- Export notes** (requires `notes:export` RBAC action):

```python
@router.get("/notes/export")
async def export_notes(
    user: AuthenticatedUser = Depends(duar.require_action("notes:export")),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
):
    return {"notes": [asdict(n) for n in models.list_by_workspace(workspace_id)]}
```

An admin must create a role with `notes:export` and assign it to users via the admin panel.

**Tier 3 -- View a single note** (entity ACL check):

```python
@router.get("/notes/{note_id}")
async def get_note(
    note_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    token: str = Depends(get_token),
):
    note = models.get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    allowed = await duar.permissions.can(
        token=token, resource_type="note",
        resource_id=note_id, action="view",
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")
    return asdict(note)
```

Include the router:

```python
# Add to backend/main.py
from routes import router
app.include_router(router)
```

## Step 4: React Frontend

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install @duar-auth/js @duar-auth/react react-router-dom @tanstack/react-query
```

### Auth Client

```typescript
// src/api/client.ts
import { DuarAuthz, IdpConfigs } from "@duar-auth/js";

const DUAR_URL = import.meta.env.VITE_DUAR_URL || "http://localhost:9003";
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:9200";
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

export const authzClient = new DuarAuthz({
  duarUrl: DUAR_URL,
  // Mint endpoint on YOUR backend — it holds the Duar service key and
  // proxies the credential-issuance call. Browsers must not mint directly.
  mintEndpoint: `${BACKEND_URL}/auth/mint`,
  idps: { google: IdpConfigs.google(GOOGLE_CLIENT_ID) },
});

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  return authzClient.fetchJson<T>(`${BACKEND_URL}${path}`, options);
}
```

`fetchJson()` attaches both tokens (`Authorization` + `X-Authz-Token`), retries on 401, and throws on errors.

### Provider

```tsx
// src/main.tsx
import { AuthzProvider } from "@duar-auth/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { authzClient } from "./api/client";

createRoot(document.getElementById("root")!).render(
  <AuthzProvider client={authzClient}>
    <QueryClientProvider client={new QueryClient()}>
      <BrowserRouter><App /></BrowserRouter>
    </QueryClientProvider>
  </AuthzProvider>,
);
```

### Routes with AuthzGuard

```tsx
// src/App.tsx
import { AuthzGuard } from "@duar-auth/react";
import { Route, Routes } from "react-router-dom";

export function App() {
  return (
    <Routes>
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route path="*" element={
        <AuthzGuard fallback={<Login />} loading={<div>Loading...</div>}>
          <Routes>
            <Route path="/" element={<NoteList />} />
            <Route path="/notes/:id" element={<NoteDetail />} />
          </Routes>
        </AuthzGuard>
      } />
    </Routes>
  );
}
```

### Login

```tsx
// src/pages/Login.tsx
import { useAuthz } from "@duar-auth/react";

export function Login() {
  const { login } = useAuthz();
  return <button onClick={() => login("google")}>Sign in with Google</button>;
}
```

### OAuth Callback

`AuthzCallback` handles the IdP redirect, token exchange, workspace selection, and calls `onSuccess`.

```tsx
// src/pages/AuthCallback.tsx
import { AuthzCallback } from "@duar-auth/react";
import { useNavigate } from "react-router-dom";

export function AuthCallback() {
  const navigate = useNavigate();
  return (
    <AuthzCallback
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

### Note List

```tsx
// src/pages/NoteList.tsx
import { useAuthzUser } from "@duar-auth/react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export function NoteList() {
  const user = useAuthzUser();
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
        {notes.map((n) => <li key={n.id}>{n.title} (by {n.owner_name})</li>)}
      </ul>
    </div>
  );
}
```

## Step 5: Run It

```bash
# Terminal 1: backend
cd backend && uvicorn main:app --port 9200 --reload

# Terminal 2: frontend
cd frontend && npm run dev
```

## Result

| Tier | What | Backend API | Frontend |
|------|------|-------------|----------|
| **1 - Workspace Role** | List notes (any user) | `get_workspace_id` | Any authenticated user |
| **1 - Workspace Role** | Create notes (editor+) | `require_role("editor")` | `useAuthzUser().workspaceRole` |
| **2 - Custom RBAC** | Export notes | `duar.require_action("notes:export")` | Call `/notes/export` |
| **3 - Entity ACL** | View a single note | `duar.permissions.can(...)` | 403 if denied |
| **3 - Entity ACL** | Register resource on create | `duar.permissions.register_resource(...)` | -- |

The complete working demo is in the `demo-authz/` directory of this repository.
