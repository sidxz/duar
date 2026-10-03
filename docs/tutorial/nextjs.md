# Tutorial: Next.js

Build the same Team Notes app from the [React tutorial](react.md), but with a Next.js App Router frontend. Same backend, different frontend stack.

**What you'll build:** A Next.js frontend with Edge Middleware for route protection, server components for data fetching, and client components for interactive UI -- all using `@duar-auth/nextjs`.

## Prerequisites

- Same as the [React tutorial](react.md#prerequisites)
- Completed backend from [React tutorial Steps 1-3](react.md#step-1-backend-setup) (or use `demo-authz/backend/`)
- Client app registered with redirect URI `http://localhost:3000/auth/callback`
- Node.js 18+

## Step 1: Backend

Use the same FastAPI backend from the React tutorial. The backend is framework-agnostic -- it validates dual tokens regardless of what frontend sends them.

If you haven't built it yet, follow [React tutorial Steps 1-3](react.md#step-1-backend-setup).

## Step 2: Next.js Setup

```bash
npx create-next-app@latest frontend --app --typescript --tailwind
cd frontend
npm install @duar-auth/js @duar-auth/nextjs
```

### Auth Client

Create a shared `DuarAuthz` instance. This is the same client from the React tutorial -- `@duar-auth/nextjs` re-exports the React hooks and wraps them with Next.js-specific helpers.

```typescript
// lib/auth.ts
import { DuarAuthz, IdpConfigs } from "@duar-auth/js";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:9200";

export const authzClient = new DuarAuthz({
  duarUrl: process.env.NEXT_PUBLIC_DUAR_URL || "http://localhost:9003",
  // Mint endpoint on YOUR backend. Browsers do not hold the Duar service
  // key; this route forwards the mint call server-to-server.
  mintEndpoint: `${BACKEND_URL}/auth/mint`,
  idps: {
    google: IdpConfigs.google(process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || ""),
  },
});

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  return authzClient.fetchJson<T>(`${BACKEND_URL}${path}`, options);
}
```

Pair this with a Next.js Route Handler (or a FastAPI route on the team-notes backend) that accepts `POST /auth/mint` and proxies to Duar with the service key — see [AuthZ Client docs](../js-sdk/authz-client.md#backend-mint-route) for the full snippet.

## Step 3: Edge Middleware

Protect routes at the edge. Unauthenticated users are redirected to `/login`.

```typescript
// middleware.ts
import { createDuarAuthzMiddleware } from "@duar-auth/nextjs/authz-middleware";

export default createDuarAuthzMiddleware({
  duarUrl: process.env.DUAR_URL!,
  idpJwksUrl: "https://www.googleapis.com/oauth2/v3/certs",
  idpAudience: process.env.GOOGLE_CLIENT_ID!,
  idpIssuer: "https://accounts.google.com",
  serviceName: "team-notes",
  publicPaths: ["/login", "/auth/callback"],
  loginPath: "/login",
});

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

`idpAudience` and `serviceName` are required: without them the middleware would accept any Google-signed token from any OAuth client, and any authz token minted for any service. See [Next.js middleware options](../js-sdk/nextjs.md#authz-middleware) for details.

## Step 4: Layout + Provider

Wrap the app in `AuthzProvider` so hooks work in client components.

```tsx
// app/layout.tsx
import { AuthzProvider } from "@duar-auth/nextjs";
import { authzClient } from "@/lib/auth";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthzProvider client={authzClient}>
          {children}
        </AuthzProvider>
      </body>
    </html>
  );
}
```

## Step 5: Pages

### Login

```tsx
// app/login/page.tsx
"use client";
import { useAuthz } from "@duar-auth/nextjs";

export default function LoginPage() {
  const { login } = useAuthz();
  return <button onClick={() => login("google")}>Sign in with Google</button>;
}
```

### OAuth Callback

```tsx
// app/auth/callback/page.tsx
"use client";
import { AuthzCallback } from "@duar-auth/nextjs";
import { useRouter } from "next/navigation";

export default function CallbackPage() {
  const router = useRouter();
  return (
    <AuthzCallback
      onSuccess={() => router.replace("/notes")}
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

A client component that uses `useAuthzUser()` for role checks and `apiFetch` for data.

```tsx
// app/notes/page.tsx
"use client";
import { useAuthzUser } from "@duar-auth/nextjs";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/auth";
import Link from "next/link";

export default function NotesPage() {
  const user = useAuthzUser();
  const [notes, setNotes] = useState<any[]>([]);
  const canCreate = ["editor", "admin", "owner"].includes(user.workspaceRole);

  useEffect(() => {
    apiFetch<any[]>("/notes").then(setNotes);
  }, []);

  return (
    <div>
      <h1>Notes</h1>
      {canCreate && <button>New Note</button>}
      <ul>
        {notes.map((note) => (
          <li key={note.id}>
            <Link href={`/notes/${note.id}`}>{note.title}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

### Note Detail

Entity-level ACL checks happen on the backend. If `permissions.can()` denies access, the API returns 403 and the frontend shows the error.

```tsx
// app/notes/[id]/page.tsx
"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/auth";
import Link from "next/link";

export default function NoteDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [note, setNote] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<any>(`/notes/${id}`)
      .then(setNote)
      .catch((e) => setError(e.message));
  }, [id]);

  if (error) return <p>Access denied: {error}</p>;
  if (!note) return <p>Loading...</p>;

  return (
    <div>
      <Link href="/notes">Back</Link>
      <h1>{note.title}</h1>
      <p>{note.content}</p>
      <p>by {note.owner_name}</p>
    </div>
  );
}
```

## Step 6: Run It

```bash
# Terminal 1: backend (same as React tutorial)
cd backend && uvicorn main:app --port 9200 --reload

# Terminal 2: Next.js frontend
cd frontend && npm run dev
```

## Result

| Component | React version | Next.js version |
|-----------|--------------|-----------------|
| Provider | `AuthzProvider` from `@duar-auth/react` | `AuthzProvider` from `@duar-auth/nextjs` |
| Route guard | `AuthzGuard` in JSX | `withDuarAuthz` Edge Middleware |
| Hooks | `useAuthz`, `useAuthzUser` from `@duar-auth/react` | Same hooks from `@duar-auth/nextjs` |
| Callback | `AuthzCallback` from `@duar-auth/react` | `AuthzCallback` from `@duar-auth/nextjs` |
| Data fetching | React Query + `apiFetch` | `apiFetch` (or React Query) |

The backend is identical. The authorization model (three tiers) is backend-enforced and frontend-agnostic.
