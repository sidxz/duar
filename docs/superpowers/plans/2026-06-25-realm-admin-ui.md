# Realm Admin UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a React admin UI for managing realms (trusted app groups) — list/create, detail with edit + member management, and a realm line on the Service App detail page — consuming the already-shipped `/admin/realms` backend.

**Architecture:** Pure frontend work in the `admin/` SPA. Mirror the existing Organizations pages (list + create + detail) and the Workspace group-members dropdown pattern. All data flows through `admin/src/api/client.ts` (a single `request<T>` helper that bakes in the `X-Requested-With` CSRF header) and React Query. No new components or dependencies — reuse `DataTable`, `Modal`, `ConfirmModal`, `StatusBadge`.

**Tech Stack:** Vite 7 + React 19 + TypeScript + TailwindCSS 4 + TanStack React Query 5 + React Router 7 + Sonner toasts.

## Global Constraints

- **No frontend test harness exists.** Each task is gated by `cd admin && npm run build` (`tsc -b && vite build` — full type-check) **and** `cd admin && npm run lint` (`eslint .`). Both must pass before commit. There are no `test_*` files to write for the SPA.
- **Stage only the `admin/` files you touch.** NEVER `git add -A` / `git add .`. NEVER run `make fmt`, `ruff format .`, or any whole-repo formatter.
- **NEVER touch** `service/src/services/role_service.py` or `service/tests/test_register_actions.py` (the user's uncommitted work).
- **Backend is frozen for this plan** — do not modify any file under `service/`. The API surface below already exists and is committed.
- **Commit message convention:** conventional commits, scope `realm`, e.g. `feat(realm): ...`. End every commit message with:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```
- **Codebase conventions:** named exports for page components (`export function RealmDetail()`), 2-space indent, double-quoted strings, `import type { ... }` for type-only imports, query-key arrays of the form `["realm", id]`.

## Backend API surface this plan consumes (already shipped — do NOT change)

All under admin cookie auth + `X-Requested-With` CSRF (handled by `request<T>`):

| Method & path | Returns | Body |
|---|---|---|
| `GET /admin/realms` | `RealmResponse[]` | — |
| `POST /admin/realms` | `RealmResponse` (201) | `{ name, slug, m2m_ttl_s? }` |
| `GET /admin/realms/{id}` | `RealmResponse` | — |
| `PATCH /admin/realms/{id}` | `RealmResponse` | `{ name?, m2m_ttl_s?, is_active? }` (no slug) |
| `DELETE /admin/realms/{id}` | 204 | — |
| `GET /admin/realms/{id}/members` | `RealmMemberResponse[]` | — |
| `POST /admin/realms/{id}/members/{serviceAppId}` | 201 | — |
| `DELETE /admin/realms/{id}/members/{serviceAppId}` | 204 | — |

- `RealmResponse` = `{ id, slug, name, m2m_ttl_s, is_active, created_at }`.
- `RealmMemberResponse` = `{ id, name, service_name, has_grants }` where **`id` is the service-app id** (this is what the add/remove member endpoints take as `{serviceAppId}`).
- `ServiceAppResponse` now includes `realm_id: string | null`.
- Slug is letter-start, lowercase letters/digits/hyphens, **immutable after create** (PATCH has no slug).
- `m2m_ttl_s` is an integer in `[30, 3600]`, default `300`.
- Errors (duplicate slug, 409 one-realm-max, validation) arrive as `{ detail }` and surface through the existing `request<T>` error path as a thrown `Error` whose message is the detail — render via `toast.error` / inline like the Organizations pages do.

## File Structure

- **Modify** `admin/src/types/api.ts` — add `Realm`, `RealmMember` types; add `realm_id` to `ServiceApp`.
- **Modify** `admin/src/api/client.ts` — add the Realms API functions.
- **Create** `admin/src/pages/Realms.tsx` — list + create (model: `Organizations.tsx`).
- **Create** `admin/src/pages/RealmDetail.tsx` — detail + edit + members + delete (model: `OrganizationDetail.tsx` + Workspace group-members dropdown).
- **Modify** `admin/src/App.tsx` — two `<Route>`s.
- **Modify** `admin/src/components/Layout.tsx` — one `NAV` entry.
- **Modify** `admin/src/pages/ServiceAppDetail.tsx` — a "Realm:" line in the info block.

## Deliberate simplification (carry forward, surface to user)

The handoff mentioned "warn when a candidate/member `has_grants`". `has_grants` is computed **only** by the realm-members endpoint (`RealmMemberResponse`), not by `GET /admin/service-apps` (`ServiceAppResponse` has no `has_grants`). So:
- **Member rows** show a `⚠ has own grants` badge (we have the field). ✅
- **Candidate dropdown** (from `getServiceApps()`) cannot pre-warn — it would need `ServiceAppResponse.has_grants`, which is backend scope. **Deferred.** The post-add member badge surfaces the same fact one click later.

`// ponytail: candidate pre-add has_grants warning skipped — needs ServiceAppResponse.has_grants (backend). Member-row badge covers it post-add.`

---

### Task 1: Types + Realm API client

**Files:**
- Modify: `admin/src/types/api.ts`
- Modify: `admin/src/api/client.ts`

**Interfaces:**
- Consumes: the `request<T>` helper in `client.ts`; existing `ServiceApp` interface.
- Produces (later tasks rely on these exact names/signatures):
  - Types `Realm = { id, slug, name, m2m_ttl_s, is_active, created_at }`, `RealmMember = { id, name, service_name, has_grants }`, and `ServiceApp` gains `realm_id: string | null`.
  - Functions `getRealms()`, `createRealm(body)`, `getRealm(id)`, `updateRealm(id, body)`, `deleteRealm(id)`, `getRealmMembers(id)`, `addRealmMember(realmId, serviceAppId)`, `removeRealmMember(realmId, serviceAppId)`.

- [ ] **Step 1: Add `realm_id` to the `ServiceApp` interface**

In `admin/src/types/api.ts`, the `ServiceApp` interface currently ends:

```ts
export interface ServiceApp {
  id: string;
  name: string;
  service_name: string;
  key_prefix: string;
  is_active: boolean;
  allowed_origins: string[];
  last_used_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}
```

Add the `realm_id` field (mirrors backend `ServiceAppResponse.realm_id`):

```ts
export interface ServiceApp {
  id: string;
  name: string;
  service_name: string;
  key_prefix: string;
  is_active: boolean;
  allowed_origins: string[];
  realm_id: string | null;
  last_used_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}
```

- [ ] **Step 2: Add the `Realm` and `RealmMember` types**

At the end of `admin/src/types/api.ts`, append a new section:

```ts
// ── Realms (trusted app groups) ──────────────────────────────────────

export interface Realm {
  id: string;
  slug: string;
  name: string;
  m2m_ttl_s: number;
  is_active: boolean;
  created_at: string;
}

export interface RealmMember {
  // `id` is the service-app id — pass it to add/removeRealmMember.
  id: string;
  name: string;
  service_name: string;
  has_grants: boolean;
}
```

- [ ] **Step 3: Import the new types in `client.ts`**

In `admin/src/api/client.ts`, the top `import type { ... }` block lists names roughly alphabetically. Add `Realm,` and `RealmMember,` between `PaginatedResponse,` and `RoleMember,`:

```ts
  PaginatedResponse,
  Realm,
  RealmMember,
  RoleMember,
```

- [ ] **Step 4: Add the Realms API functions**

At the end of `admin/src/api/client.ts`, append:

```ts
// ── Realms ───────────────────────────────────────────────────────────

export const getRealms = () => request<Realm[]>("/admin/realms");

export const createRealm = (body: {
  name: string;
  slug: string;
  m2m_ttl_s?: number;
}) =>
  request<Realm>("/admin/realms", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getRealm = (id: string) => request<Realm>(`/admin/realms/${id}`);

export const updateRealm = (
  id: string,
  body: { name?: string; m2m_ttl_s?: number; is_active?: boolean },
) =>
  request<Realm>(`/admin/realms/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteRealm = (id: string) =>
  request(`/admin/realms/${id}`, { method: "DELETE" });

export const getRealmMembers = (id: string) =>
  request<RealmMember[]>(`/admin/realms/${id}/members`);

export const addRealmMember = (realmId: string, serviceAppId: string) =>
  request(`/admin/realms/${realmId}/members/${serviceAppId}`, {
    method: "POST",
  });

export const removeRealmMember = (realmId: string, serviceAppId: string) =>
  request(`/admin/realms/${realmId}/members/${serviceAppId}`, {
    method: "DELETE",
  });
```

- [ ] **Step 5: Build + lint**

Run: `cd admin && npm run build && npm run lint`
Expected: both pass with no errors. (New exports are unused so far — that is fine; exported symbols don't trip `no-unused-vars`.)

- [ ] **Step 6: Commit**

```bash
git add admin/src/types/api.ts admin/src/api/client.ts
git commit -m "feat(realm): admin SPA types + realm API client

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Realms list + create page (route + nav)

**Files:**
- Create: `admin/src/pages/Realms.tsx`
- Modify: `admin/src/App.tsx`
- Modify: `admin/src/components/Layout.tsx`

**Interfaces:**
- Consumes: `getRealms`, `createRealm`, type `Realm` (Task 1); `DataTable`, `Modal`, `StatusBadge` components.
- Produces: route `/realms` rendering `<Realms />`; nav entry to `/realms`. The component navigates to `/realms/:id` on row-click and after create (the target route lands in Task 3).

- [ ] **Step 1: Create `admin/src/pages/Realms.tsx`**

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { createRealm, getRealms } from "../api/client";
import type { Realm } from "../types/api";
import { DataTable } from "../components/DataTable";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/Badge";

export function Realms() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", slug: "", m2m_ttl_s: "300" });

  const { data: realms = [], isLoading } = useQuery({
    queryKey: ["realms"],
    queryFn: getRealms,
  });

  const create = useMutation({
    mutationFn: () =>
      createRealm({
        name: form.name,
        slug: form.slug,
        m2m_ttl_s: Number(form.m2m_ttl_s),
      }),
    onSuccess: (realm: Realm) => {
      queryClient.invalidateQueries({ queryKey: ["realms"] });
      setShowCreate(false);
      setForm({ name: "", slug: "", m2m_ttl_s: "300" });
      toast.success("Realm created");
      navigate(`/realms/${realm.id}`);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const columns = [
    {
      key: "name",
      header: "Name",
      render: (r: Realm) => <span className="font-medium text-sm">{r.name}</span>,
    },
    {
      key: "slug",
      header: "Slug",
      render: (r: Realm) => (
        <code className="text-xs text-zinc-400 font-mono">{r.slug}</code>
      ),
    },
    {
      key: "m2m_ttl_s",
      header: "m2m TTL",
      render: (r: Realm) => (
        <span className="text-sm text-zinc-300">{r.m2m_ttl_s}s</span>
      ),
      className: "w-24",
    },
    {
      key: "is_active",
      header: "Active",
      render: (r: Realm) => <StatusBadge active={r.is_active} />,
      className: "w-28",
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Realms</h1>
          <p className="text-sm text-zinc-500">
            Trusted app groups that share one permission scope. Member services
            read and write permissions under the realm slug, and the realm mints
            no-user (m2m) tokens for them.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white transition-colors"
        >
          New realm
        </button>
      </div>

      {isLoading ? (
        <div className="h-64 bg-zinc-800/30 rounded-lg animate-pulse" />
      ) : (
        <DataTable
          columns={columns}
          data={realms}
          onRowClick={(r) => navigate(`/realms/${r.id}`)}
          emptyMessage="No realms"
        />
      )}

      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="New Realm">
        <div className="space-y-3">
          <div>
            <label className="text-xs text-zinc-500">Display Name</label>
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Acme Suite"
              className="mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-600"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-500">
              Slug (the shared permission scope — immutable)
            </label>
            <input
              value={form.slug}
              onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))}
              placeholder="acme-suite"
              className="mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 font-mono placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-600"
            />
            <p className="mt-1 text-xs text-zinc-600">
              Starts with a letter; lowercase letters, digits, and hyphens.
            </p>
          </div>
          <div>
            <label className="text-xs text-zinc-500">m2m token TTL (seconds)</label>
            <input
              type="number"
              min={30}
              max={3600}
              value={form.m2m_ttl_s}
              onChange={(e) =>
                setForm((f) => ({ ...f, m2m_ttl_s: e.target.value }))
              }
              className="mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 font-mono placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-600"
            />
            <p className="mt-1 text-xs text-zinc-600">Between 30 and 3600. Default 300.</p>
          </div>
          {create.isError && (
            <div className="text-xs text-red-400">
              {(create.error as Error).message}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={() => setShowCreate(false)}
              className="px-3 py-1.5 rounded text-xs text-zinc-400 hover:text-zinc-200"
            >
              Cancel
            </button>
            <button
              onClick={() => create.mutate()}
              disabled={!form.name || !form.slug || create.isPending}
              className="px-3 py-1.5 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50 transition-colors"
            >
              {create.isPending ? "Creating..." : "Create"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
```

- [ ] **Step 2: Register the `/realms` list route in `App.tsx`**

Add ONLY the list route + import in this task — `RealmDetail` and the `/realms/:id` route land in Task 3, so that each task stays independently buildable.

In `admin/src/App.tsx`, add the import after the `Organizations`/`OrganizationDetail` imports:

```tsx
import { Realms } from "./pages/Realms";
```

Then add the list route after the `organizations/:id` route:

```tsx
          <Route path="/organizations/:id" element={<OrganizationDetail />} />
          <Route path="/realms" element={<Realms />} />
```

- [ ] **Step 3: Add the nav entry in `Layout.tsx`**

In `admin/src/components/Layout.tsx`, the `NAV` array has an entry for `/service-apps` labelled "Services". Add a Realms entry right after it (squares-2x2 heroicon):

```tsx
  { to: "/service-apps", label: "Services", icon: "M21 12a2.25 2.25 0 00-2.25-2.25H15a3 3 0 11-6 0H5.25A2.25 2.25 0 003 12m18 0v6a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 18v-6m18 0V9M3 12V9m18 0a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 013 9m18 0V6a2.25 2.25 0 00-2.25-2.25H5.25A2.25 2.25 0 013 6v3" },
  { to: "/realms", label: "Realms", icon: "M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" },
```

- [ ] **Step 4: Build + lint**

Run: `cd admin && npm run build && npm run lint`
Expected: both pass.

- [ ] **Step 5: Manual smoke (optional, if dev server is running)**

`make admin` → open the panel → "Realms" appears in the sidebar → the list renders (empty "No realms" or seeded rows) → "New realm" opens the modal → creating one toasts success and navigates to `/realms/<id>` (which 404s into an empty route until Task 3 — expected).

- [ ] **Step 6: Commit**

```bash
git add admin/src/pages/Realms.tsx admin/src/App.tsx admin/src/components/Layout.tsx
git commit -m "feat(realm): Realms list + create page, nav, route

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Realm detail page (edit + members + delete)

**Files:**
- Create: `admin/src/pages/RealmDetail.tsx`
- Modify: `admin/src/App.tsx`

**Interfaces:**
- Consumes: `getRealm`, `updateRealm`, `deleteRealm`, `getRealmMembers`, `addRealmMember`, `removeRealmMember`, `getServiceApps`, types `RealmMember`/`ServiceApp` (Task 1); `ConfirmModal`, `Modal`, `StatusBadge`.
- Produces: route `/realms/:id` rendering `<RealmDetail />`.
- Query keys used: `["realm", id]`, `["realm-members", id]`, `["service-apps"]`. Mutations invalidate `["realm", id]`, `["realm-members", id]`, `["realms"]`, and `["service-apps"]` (membership flips a service app's `realm_id`, so the candidate list and any Services view must reseed).

- [ ] **Step 1: Create `admin/src/pages/RealmDetail.tsx`**

```tsx
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  addRealmMember,
  deleteRealm,
  getRealm,
  getRealmMembers,
  getServiceApps,
  removeRealmMember,
  updateRealm,
} from "../api/client";
import type { RealmMember } from "../types/api";
import { ConfirmModal } from "../components/ConfirmModal";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/Badge";

export function RealmDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [showEdit, setShowEdit] = useState(false);
  const [editForm, setEditForm] = useState({
    name: "",
    m2m_ttl_s: "300",
    is_active: true,
  });
  const [showDelete, setShowDelete] = useState(false);
  const [deleteSlug, setDeleteSlug] = useState("");
  const [selectedApp, setSelectedApp] = useState("");

  const {
    data: realm,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["realm", id],
    queryFn: () => getRealm(id!),
    enabled: !!id,
  });

  const { data: members = [] } = useQuery({
    queryKey: ["realm-members", id],
    queryFn: () => getRealmMembers(id!),
    enabled: !!id,
  });

  const { data: allApps = [] } = useQuery({
    queryKey: ["service-apps"],
    queryFn: getServiceApps,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["realm", id] });
    queryClient.invalidateQueries({ queryKey: ["realm-members", id] });
    queryClient.invalidateQueries({ queryKey: ["realms"] });
    queryClient.invalidateQueries({ queryKey: ["service-apps"] });
  };

  const update = useMutation({
    mutationFn: () =>
      updateRealm(id!, {
        name: editForm.name || undefined,
        m2m_ttl_s: Number(editForm.m2m_ttl_s),
        is_active: editForm.is_active,
      }),
    onSuccess: () => {
      invalidate();
      setShowEdit(false);
      toast.success("Realm updated");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const addMember = useMutation({
    mutationFn: (serviceAppId: string) => addRealmMember(id!, serviceAppId),
    onSuccess: () => {
      invalidate();
      setSelectedApp("");
      toast.success("Service added to realm");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const removeMember = useMutation({
    mutationFn: (serviceAppId: string) => removeRealmMember(id!, serviceAppId),
    onSuccess: () => {
      invalidate();
      toast.success("Service removed from realm");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: () => deleteRealm(id!),
    onSuccess: () => {
      setShowDelete(false);
      queryClient.invalidateQueries({ queryKey: ["realms"] });
      queryClient.invalidateQueries({ queryKey: ["service-apps"] });
      toast.success("Realm deleted");
      navigate("/realms");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const openEdit = () => {
    if (realm) {
      setEditForm({
        name: realm.name,
        m2m_ttl_s: String(realm.m2m_ttl_s),
        is_active: realm.is_active,
      });
    }
    setShowEdit(true);
  };

  if (isLoading) {
    return <div className="h-64 bg-zinc-800/30 rounded-lg animate-pulse" />;
  }

  if (isError || !realm) {
    return (
      <div className="space-y-3 max-w-3xl">
        <Link to="/realms" className="text-sm text-zinc-500 hover:text-zinc-300">
          ← Realms
        </Link>
        <div className="border border-zinc-800 rounded-lg p-6 text-center">
          <p className="text-sm text-zinc-300">
            {(error as Error)?.message ?? "Realm not found."}
          </p>
        </div>
      </div>
    );
  }

  // A service belongs to at most one realm — only standalone apps are candidates.
  // ponytail: candidate pre-add has_grants warning skipped — needs
  // ServiceAppResponse.has_grants (backend). Member-row badge covers it post-add.
  const candidates = allApps.filter((a) => a.realm_id == null);

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <Link to="/realms" className="hover:text-zinc-300">
          Realms
        </Link>
        <span>/</span>
        <span className="text-zinc-200">{realm.name}</span>
      </div>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold">{realm.name}</h1>
          <code className="text-xs text-zinc-500 font-mono">{realm.slug}</code>
          <p className="mt-1 text-xs text-zinc-500">
            m2m token TTL: {realm.m2m_ttl_s}s
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge active={realm.is_active} />
          <button
            onClick={openEdit}
            className="px-3 py-1.5 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white transition-colors"
          >
            Edit
          </button>
        </div>
      </div>

      {/* Members */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-zinc-300">Member services</h2>
        {members.length === 0 ? (
          <p className="text-sm text-zinc-500">
            No services yet — add a standalone service below. Members share this
            realm's permission scope.
          </p>
        ) : (
          <ul className="space-y-1">
            {members.map((m: RealmMember) => (
              <li
                key={m.id}
                className="flex items-center justify-between border border-zinc-800 rounded-md px-3 py-2"
              >
                <span className="text-sm">
                  <span className="text-zinc-200">{m.name}</span>
                  <code className="ml-2 text-xs text-zinc-500 font-mono">
                    {m.service_name}
                  </code>
                  {m.has_grants && (
                    <span
                      className="ml-2 text-xs text-amber-400"
                      title="This service has its own permission grants, which are NOT visible under the realm scope."
                    >
                      ⚠ has own grants
                    </span>
                  )}
                </span>
                <button
                  onClick={() => removeMember.mutate(m.id)}
                  className="text-xs text-red-400 hover:text-red-300"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="flex items-end gap-2">
          <div className="flex-1">
            <label className="text-xs text-zinc-500">Add a standalone service</label>
            <select
              value={selectedApp}
              onChange={(e) => setSelectedApp(e.target.value)}
              className="mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-600"
            >
              <option value="">Select a service…</option>
              {candidates.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.service_name})
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={() => {
              if (selectedApp) addMember.mutate(selectedApp);
            }}
            disabled={!selectedApp || addMember.isPending}
            className="px-3 py-2 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50 transition-colors"
          >
            Add
          </button>
        </div>
      </section>

      {/* Danger zone */}
      <section className="space-y-2 border-t border-zinc-800 pt-4">
        <h2 className="text-sm font-semibold text-red-400">Danger zone</h2>
        <p className="text-sm text-zinc-500">
          Deleting a realm un-assigns its member services (their{" "}
          <code className="font-mono">realm_id</code> is cleared). Permissions
          written under the realm scope are NOT deleted.
        </p>
        <button
          onClick={() => {
            setDeleteSlug("");
            setShowDelete(true);
          }}
          className="px-3 py-1.5 rounded text-xs font-medium bg-red-500/10 text-red-400 hover:bg-red-500/20 ring-1 ring-red-500/20 transition-colors"
        >
          Delete realm
        </button>
      </section>

      {/* Edit modal */}
      <Modal open={showEdit} onClose={() => setShowEdit(false)} title="Edit Realm">
        <div className="space-y-3">
          <div>
            <label className="text-xs text-zinc-500">Name</label>
            <input
              value={editForm.name}
              onChange={(e) =>
                setEditForm((f) => ({ ...f, name: e.target.value }))
              }
              className="mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-600"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-500">m2m token TTL (seconds)</label>
            <input
              type="number"
              min={30}
              max={3600}
              value={editForm.m2m_ttl_s}
              onChange={(e) =>
                setEditForm((f) => ({ ...f, m2m_ttl_s: e.target.value }))
              }
              className="mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 font-mono focus:outline-none focus:ring-1 focus:ring-zinc-600"
            />
            <p className="mt-1 text-xs text-zinc-600">Between 30 and 3600.</p>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="realm_is_active"
              checked={editForm.is_active}
              onChange={(e) =>
                setEditForm((f) => ({ ...f, is_active: e.target.checked }))
              }
              className="rounded border-zinc-600 bg-zinc-800 text-zinc-300"
            />
            <label htmlFor="realm_is_active" className="text-xs text-zinc-400">
              Active
            </label>
          </div>
          <p className="text-xs text-zinc-600">
            Slug is immutable and cannot be changed.
          </p>
          {update.isError && (
            <div className="text-xs text-red-400">
              {(update.error as Error).message}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={() => setShowEdit(false)}
              className="px-3 py-1.5 rounded text-xs text-zinc-400 hover:text-zinc-200"
            >
              Cancel
            </button>
            <button
              onClick={() => update.mutate()}
              disabled={update.isPending}
              className="px-3 py-1.5 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50"
            >
              Save
            </button>
          </div>
        </div>
      </Modal>

      {/* Delete confirmation */}
      <ConfirmModal
        open={showDelete}
        onClose={() => setShowDelete(false)}
        onConfirm={() => remove.mutate()}
        title="Delete Realm"
        message={`This permanently deletes "${realm.name}" and clears realm_id on its member services. Type the slug to confirm.`}
        confirmLabel="Delete Realm"
        danger
        isPending={remove.isPending}
        confirmInput={realm.slug}
        confirmInputValue={deleteSlug}
        onConfirmInputChange={setDeleteSlug}
      />
    </div>
  );
}
```

- [ ] **Step 2: Wire the `/realms/:id` route in `App.tsx`**

Add the import (next to the `Realms` import from Task 2):

```tsx
import { RealmDetail } from "./pages/RealmDetail";
```

Add the route right after the `/realms` list route:

```tsx
          <Route path="/realms" element={<Realms />} />
          <Route path="/realms/:id" element={<RealmDetail />} />
```

- [ ] **Step 3: Build + lint**

Run: `cd admin && npm run build && npm run lint`
Expected: both pass.

- [ ] **Step 4: Manual smoke (optional, if dev server is running)**

Open a realm → header shows name/slug/TTL/status → Edit changes name/TTL/active and toasts → the member dropdown lists only standalone services → Add moves a service in (it disappears from the dropdown, appears in the list; a service with prior grants shows the ⚠ badge) → Remove takes it back out → Delete requires typing the slug, then returns to `/realms`.

- [ ] **Step 5: Commit**

```bash
git add admin/src/pages/RealmDetail.tsx admin/src/App.tsx
git commit -m "feat(realm): Realm detail page (edit, members, delete)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Show a service's realm on the Service App detail page

**Files:**
- Modify: `admin/src/pages/ServiceAppDetail.tsx`

**Interfaces:**
- Consumes: `getRealms` + type `Realm` (Task 1); the existing `app.realm_id` field (Task 1).
- Produces: a "Realm:" line in the Service App info block, resolving `realm_id` → realm name client-side (the API intentionally has no `realm_name`).

- [ ] **Step 1: Import `getRealms` and load the realm list**

In `admin/src/pages/ServiceAppDetail.tsx`, add `getRealms` to the existing import from `../api/client`:

```tsx
import {
  deleteServiceApp,
  getRealms,
  getServiceApp,
  purgeServicePermissions,
  rotateServiceAppKey,
  updateServiceApp,
} from "../api/client";
```

Inside `ServiceAppDetail()`, after the existing `app` query, add a realms query and resolve the name:

```tsx
  const { data: realms = [] } = useQuery({
    queryKey: ["realms"],
    queryFn: getRealms,
  });
  const realmName = app?.realm_id
    ? (realms.find((r) => r.id === app.realm_id)?.name ?? app.realm_id)
    : null;
```

- [ ] **Step 2: Render the "Realm:" line in the info block**

In the info block (the `<div className="mt-2 space-y-1">` group, after the "Key:" line and before "Last used:"), add:

```tsx
              {realmName && (
                <div className="text-xs text-zinc-500">
                  Realm:{" "}
                  <Link
                    to={`/realms/${app.realm_id}`}
                    className="text-zinc-400 hover:text-zinc-200 font-mono"
                  >
                    {realmName}
                  </Link>
                </div>
              )}
```

`Link` is already imported in this file (`import { Link, useNavigate, useParams } from "react-router-dom";`). `useQuery` is already imported.

- [ ] **Step 3: Build + lint**

Run: `cd admin && npm run build && npm run lint`
Expected: both pass.

- [ ] **Step 4: Manual smoke (optional, if dev server is running)**

Open a service that belongs to a realm (add one via Task 3 first) → its detail page shows a "Realm: <name>" line linking to `/realms/<id>` → a standalone service shows no Realm line.

- [ ] **Step 5: Commit**

```bash
git add admin/src/pages/ServiceAppDetail.tsx
git commit -m "feat(realm): show realm membership on Service detail

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** (spec build-sequence item 5 = "Admin — /admin/realms CRUD + membership, React Realms page, Service App detail"):
- Realms list + create → Task 2. ✅
- Realm detail edit (name, m2m_ttl_s, is_active) → Task 3. ✅
- Membership add/remove (dropdown of standalone apps, per-row remove) → Task 3. ✅
- `has_grants` warning → Task 3 member badge (candidate pre-warn deferred — see "Deliberate simplification"). ⚠ surfaced
- Delete with type-to-confirm → Task 3. ✅
- Service App detail shows realm → Task 4. ✅
- Nav + routes → Tasks 2 & 3. ✅

**Placeholder scan:** No TBD/TODO/"add error handling" — every step has full code. Errors are handled via the existing `request<T>` throw + `toast.error`/inline, matching the Organizations pages.

**Type consistency:** `Realm`/`RealmMember` field names match the backend response models and are used identically across Tasks 2–4. `removeRealmMember(realmId, member.id)` passes the service-app id (verified: `RealmMemberResponse.id == service_app.id`). `addRealmMember`/`removeRealmMember` signatures `(realmId, serviceAppId)` are consistent between client.ts and RealmDetail. Query keys `["realms"]`, `["realm", id]`, `["realm-members", id]`, `["service-apps"]` are consistent and invalidated together on membership changes.
