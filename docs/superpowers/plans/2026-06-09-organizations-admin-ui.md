# Organizations Admin UI (Plan 2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the React admin UI for organizations — an Organizations list + detail (domain editor, enable/public toggle, users, delete) and a per-workspace "Access" tab — consuming the Plan-2a admin API.

**Architecture:** New pages `Organizations.tsx` + `OrganizationDetail.tsx` and an `AccessTab` inside the existing `WorkspaceDetail.tsx`, all built from the existing admin building blocks (`DataTable`, `Modal`, `ConfirmModal`, `StatusBadge`, React Query, `sonner` toasts) in the dark-zinc theme. API calls + types are added to `api/client.ts` + `types/api.ts`.

**Tech Stack:** React 18, TypeScript, Vite, @tanstack/react-query, react-router-dom, TailwindCSS, sonner.

> **TESTING NOTE (read first):** The admin app has **no test runner** (no vitest/jest/testing-library; `package.json` scripts are only `dev`/`build`/`lint`/`preview`). This plan does **not** introduce one — that would be unrequested, inconsistent infra for a single feature. Verification per task is: **TypeScript typecheck** (`cd admin && npx tsc -b`) + **eslint** (`cd admin && npm run lint`), with a **manual run-through** of every interaction in the final task. The per-task spec + code-quality reviews (reviewers reading the diff) are the primary correctness gate, consistent with how the rest of the admin app is maintained.

**Spec:** `docs/superpowers/specs/2026-06-09-organizations-admin-design.md` (Plan 2b). Builds on Plan 2a (admin API, already on this branch). The spec's optional "System Settings public-sign-in mirror" is **dropped as YAGNI** — the public org's on/off state is already prominent on the Organizations list + its detail toggle.

---

## Reference patterns (all paste-ready, from the live admin app)

- **API client** `admin/src/api/client.ts`: `request<T>(path, options?)` helper, `BASE = "/api"`, sends `X-Requested-With`, throws `Error(detail)` on !ok, returns `undefined` on 204. Types live in `admin/src/types/api.ts`.
- **List page** `admin/src/pages/ServiceApps.tsx`: `useQuery` + `useMutation` (invalidate + `toast`), `DataTable` with a `columns` array, create via `<Modal>`, loading skeleton `<div className="h-64 bg-zinc-800/30 rounded-lg animate-pulse" />`.
- **Detail page** `admin/src/pages/ServiceAppDetail.tsx`: `useQuery({queryKey, queryFn, enabled: !!id})`, update/delete `useMutation`, delete via `<ConfirmModal ... confirmInput={slug} confirmInputValue=... onConfirmInputChange=... />` then `navigate(...)`.
- **Tabs** `admin/src/pages/WorkspaceDetail.tsx`: `const TABS = [...] as const; type Tab = (typeof TABS)[number];`, tab buttons, `{tab === "X" && <XTab workspaceId={id!} />}`, and per-tab components like `MembersTab`.
- **Components**: `Modal({open,onClose,title,children})`; `ConfirmModal({open,onClose,onConfirm,title,message,confirmLabel?,danger?,isPending?,confirmInput?,confirmInputValue?,onConfirmInputChange?})`; `DataTable<T>({columns,data,onRowClick?,emptyMessage?})` where `Column<T> = {key,header,render:(row)=>node,className?}`; `StatusBadge({active})`.
- **Routing** `admin/src/App.tsx` `<Routes>`; **Nav** `admin/src/components/Layout.tsx` `NAV` array.
- **Input class** (reused everywhere): `"mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-600"`.
- **Primary button**: `"px-3 py-1.5 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50"`. **Secondary**: `"px-3 py-1.5 rounded text-xs text-zinc-400 hover:text-zinc-200"`.

## File Structure

**Create:**
- `admin/src/pages/Organizations.tsx` — list page.
- `admin/src/pages/OrganizationDetail.tsx` — detail page (domains, toggle, users, delete).

**Modify:**
- `admin/src/types/api.ts` — add `Organization`, `OrgDomain`, `OrganizationDetail`, `OrgUser`.
- `admin/src/api/client.ts` — add org endpoint wrappers.
- `admin/src/App.tsx` — add `/organizations` + `/organizations/:id` routes.
- `admin/src/components/Layout.tsx` — add the Organizations nav item.
- `admin/src/pages/WorkspaceDetail.tsx` — add the "Access" tab + `AccessTab`.

---

### Task 1: API client types + wrappers

**Files:**
- Modify: `admin/src/types/api.ts`
- Modify: `admin/src/api/client.ts`

- [ ] **Step 1: Add the types** (append to `admin/src/types/api.ts`)

```typescript
export interface Organization {
  id: string;
  name: string;
  slug: string;
  is_public: boolean;
  enabled: boolean;
  domain_count: number;
  user_count: number;
}

export interface OrgDomain {
  id: string;
  domain: string;
  include_subdomains: boolean;
}

export interface OrganizationDetail {
  id: string;
  name: string;
  slug: string;
  is_public: boolean;
  enabled: boolean;
  user_count: number;
  domains: OrgDomain[];
}

export interface OrgUser {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  is_active: boolean;
}
```

- [ ] **Step 2: Add the endpoint wrappers** (append to `admin/src/api/client.ts`)

First ensure the new types are imported. The file imports types from `../types/api` — add `Organization, OrgDomain, OrganizationDetail, OrgUser` to that import list. Then append:

```typescript
export const getOrganizations = () =>
  request<Organization[]>("/admin/organizations");

export const createOrganization = (body: { name: string; slug: string }) =>
  request<Organization>("/admin/organizations", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getOrganization = (id: string) =>
  request<OrganizationDetail>(`/admin/organizations/${id}`);

export const updateOrganization = (
  id: string,
  body: { name?: string; enabled?: boolean },
) =>
  request<OrganizationDetail>(`/admin/organizations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteOrganization = (id: string) =>
  request(`/admin/organizations/${id}`, { method: "DELETE" });

export const addOrgDomain = (
  id: string,
  body: { domain: string; include_subdomains: boolean },
) =>
  request<OrgDomain>(`/admin/organizations/${id}/domains`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const removeOrgDomain = (id: string, domainId: string) =>
  request(`/admin/organizations/${id}/domains/${domainId}`, {
    method: "DELETE",
  });

export const getOrgUsers = (id: string) =>
  request<OrgUser[]>(`/admin/organizations/${id}/users`);

export const getWorkspaceAllowedOrgs = (workspaceId: string) =>
  request<{ organization_ids: string[] }>(
    `/admin/workspaces/${workspaceId}/allowed-organizations`,
  );

export const setWorkspaceAllowedOrgs = (
  workspaceId: string,
  organization_ids: string[],
) =>
  request<{ organization_ids: string[] }>(
    `/admin/workspaces/${workspaceId}/allowed-organizations`,
    { method: "PUT", body: JSON.stringify({ organization_ids }) },
  );
```

- [ ] **Step 3: Typecheck + lint**

Run: `cd /Users/sidx/workspace/identity-service/admin && npx tsc -b && npm run lint`
Expected: no errors (the new exports are unused so far — eslint in this project does not error on unused exports; if it flags unused imports, ensure every imported type is referenced by a wrapper).

- [ ] **Step 4: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add admin/src/types/api.ts admin/src/api/client.ts
git commit -m "feat(admin-ui): org API client wrappers + types

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Organizations list page + route + nav

**Files:**
- Create: `admin/src/pages/Organizations.tsx`
- Modify: `admin/src/App.tsx`
- Modify: `admin/src/components/Layout.tsx`

- [ ] **Step 1: Create the list page** (`admin/src/pages/Organizations.tsx`)

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { createOrganization, getOrganizations } from "../api/client";
import type { Organization } from "../types/api";
import { DataTable } from "../components/DataTable";
import { Modal } from "../components/Modal";
import { StatusBadge } from "../components/Badge";

export default function Organizations() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", slug: "" });

  const { data: orgs = [], isLoading } = useQuery({
    queryKey: ["organizations"],
    queryFn: getOrganizations,
  });

  const create = useMutation({
    mutationFn: () => createOrganization({ name: form.name, slug: form.slug }),
    onSuccess: (org: Organization) => {
      queryClient.invalidateQueries({ queryKey: ["organizations"] });
      setShowCreate(false);
      setForm({ name: "", slug: "" });
      toast.success("Organization created");
      navigate(`/organizations/${org.id}`);
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const columns = [
    {
      key: "name",
      header: "Name",
      render: (o: Organization) => (
        <span className="font-medium text-sm">
          {o.is_public && <span className="mr-1">🌐</span>}
          {o.name}
          {o.is_public && (
            <span className="ml-2 text-xs text-zinc-500">(public catch-all)</span>
          )}
        </span>
      ),
    },
    {
      key: "slug",
      header: "Slug",
      render: (o: Organization) => (
        <code className="text-xs text-zinc-400 font-mono">{o.slug}</code>
      ),
    },
    {
      key: "domains",
      header: "Domains",
      render: (o: Organization) => (
        <span className="text-sm text-zinc-300">
          {o.is_public ? "—" : o.domain_count}
        </span>
      ),
      className: "w-24",
    },
    {
      key: "enabled",
      header: "Enabled",
      render: (o: Organization) => <StatusBadge active={o.enabled} />,
      className: "w-28",
    },
    {
      key: "users",
      header: "Users",
      render: (o: Organization) => (
        <span className="text-sm text-zinc-300">{o.user_count}</span>
      ),
      className: "w-20",
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Organizations</h1>
          <p className="text-sm text-zinc-500">
            Email-domain tenancy. The public org is the catch-all for unclaimed
            domains; its status is the public sign-in switch.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white"
        >
          New org
        </button>
      </div>

      {isLoading ? (
        <div className="h-64 bg-zinc-800/30 rounded-lg animate-pulse" />
      ) : (
        <DataTable
          columns={columns}
          data={orgs}
          onRowClick={(o) => navigate(`/organizations/${o.id}`)}
          emptyMessage="No organizations"
        />
      )}

      <Modal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        title="New Organization"
      >
        <div className="space-y-3">
          <div>
            <label className="text-xs text-zinc-500">Display Name</label>
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Texas A&M University"
              className="mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-600"
            />
          </div>
          <div>
            <label className="text-xs text-zinc-500">
              Slug (lowercase, in the token's org claim — immutable)
            </label>
            <input
              value={form.slug}
              onChange={(e) =>
                setForm((f) => ({ ...f, slug: e.target.value }))
              }
              placeholder="tamu"
              className="mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-600"
            />
          </div>
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
              className="px-3 py-1.5 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50"
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

- [ ] **Step 2: Add routes** (`admin/src/App.tsx`)

Add the import with the other page imports:

```tsx
import Organizations from "./pages/Organizations";
import OrganizationDetail from "./pages/OrganizationDetail";
```

Add the two routes inside `<Routes>` (next to the service-apps routes):

```tsx
<Route path="/organizations" element={<Organizations />} />
<Route path="/organizations/:id" element={<OrganizationDetail />} />
```

(`OrganizationDetail` is created in Task 3. To keep this task's build green, create a one-line stub now and replace it in Task 3:)

Create `admin/src/pages/OrganizationDetail.tsx` as a temporary stub (named export — every page in this app uses named exports/imports):

```tsx
export function OrganizationDetail() {
  return null;
}
```

- [ ] **Step 3: Add the nav item** (`admin/src/components/Layout.tsx`)

Add this entry to the `NAV` array, right after the Workspaces entry:

```tsx
  { to: "/organizations", label: "Organizations", icon: "M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21" },
```

- [ ] **Step 4: Typecheck + lint**

Run: `cd /Users/sidx/workspace/identity-service/admin && npx tsc -b && npm run lint`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add admin/src/pages/Organizations.tsx admin/src/pages/OrganizationDetail.tsx admin/src/App.tsx admin/src/components/Layout.tsx
git commit -m "feat(admin-ui): organizations list page, route, nav

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Organization detail page

Replaces the Task-2 stub with the real page: header + enable/public toggle, domains editor (hidden for the public org), users-in-org, and a danger-zone delete (hidden for the public org).

**Files:**
- Modify (replace stub): `admin/src/pages/OrganizationDetail.tsx`

- [ ] **Step 1: Implement the page** (replace the entire contents of `admin/src/pages/OrganizationDetail.tsx`)

```tsx
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  addOrgDomain,
  deleteOrganization,
  getOrganization,
  getOrgUsers,
  removeOrgDomain,
  updateOrganization,
} from "../api/client";
import type { OrgDomain } from "../types/api";
import { ConfirmModal } from "../components/ConfirmModal";
import { StatusBadge } from "../components/Badge";

// Named export (codebase convention). Only the OrgDomain type is imported; the
// org object is inferred from getOrganization's return, so the page's
// `OrganizationDetail` name doesn't collide with the `OrganizationDetail` type.
export function OrganizationDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [newDomain, setNewDomain] = useState("");
  const [includeSub, setIncludeSub] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [deleteSlug, setDeleteSlug] = useState("");

  const { data: org, isLoading } = useQuery({
    queryKey: ["organization", id],
    queryFn: () => getOrganization(id!),
    enabled: !!id,
  });

  const { data: users = [] } = useQuery({
    queryKey: ["organization-users", id],
    queryFn: () => getOrgUsers(id!),
    enabled: !!id,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["organization", id] });
    queryClient.invalidateQueries({ queryKey: ["organizations"] });
  };

  const toggleEnabled = useMutation({
    mutationFn: () => updateOrganization(id!, { enabled: !org!.enabled }),
    onSuccess: () => {
      invalidate();
      toast.success("Updated");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const addDomain = useMutation({
    mutationFn: () =>
      addOrgDomain(id!, {
        domain: newDomain.trim(),
        include_subdomains: includeSub,
      }),
    onSuccess: () => {
      invalidate();
      setNewDomain("");
      setIncludeSub(false);
      toast.success("Domain added");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const removeDomain = useMutation({
    mutationFn: (domainId: string) => removeOrgDomain(id!, domainId),
    onSuccess: () => {
      invalidate();
      toast.success("Domain removed");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: () => deleteOrganization(id!),
    onSuccess: () => {
      setShowDelete(false);
      queryClient.invalidateQueries({ queryKey: ["organizations"] });
      toast.success("Organization deleted");
      navigate("/organizations");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  if (isLoading || !org) {
    return <div className="h-64 bg-zinc-800/30 rounded-lg animate-pulse" />;
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <button
        onClick={() => navigate("/organizations")}
        className="text-xs text-zinc-500 hover:text-zinc-300"
      >
        ← Organizations
      </button>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold">
            {org.is_public && <span className="mr-1">🌐</span>}
            {org.name}
          </h1>
          <code className="text-xs text-zinc-500 font-mono">{org.slug}</code>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge active={org.enabled} />
          <button
            onClick={() => toggleEnabled.mutate()}
            disabled={toggleEnabled.isPending}
            className="px-3 py-1.5 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50"
          >
            {org.enabled
              ? org.is_public
                ? "Disable public sign-in"
                : "Disable"
              : org.is_public
                ? "Enable public sign-in"
                : "Enable"}
          </button>
        </div>
      </div>

      {org.is_public && (
        <p className="text-sm text-zinc-500 border border-zinc-800 rounded-md p-3">
          This is the public catch-all organization for users whose email domain
          is not claimed by any other org. Its <b>Enabled</b> state is the global
          public-sign-in switch. It has no domains and cannot be deleted.
        </p>
      )}

      {/* Domains (real orgs only) */}
      {!org.is_public && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-zinc-300">Email domains</h2>
          {org.domains.length === 0 ? (
            <p className="text-sm text-zinc-500">
              No domains yet — users from this org can't be resolved until you add
              one.
            </p>
          ) : (
            <ul className="space-y-1">
              {org.domains.map((d: OrgDomain) => (
                <li
                  key={d.id}
                  className="flex items-center justify-between border border-zinc-800 rounded-md px-3 py-2"
                >
                  <span className="text-sm">
                    <code className="font-mono text-zinc-200">{d.domain}</code>
                    {d.include_subdomains && (
                      <span className="ml-2 text-xs text-emerald-400">
                        +subdomains
                      </span>
                    )}
                  </span>
                  <button
                    onClick={() => removeDomain.mutate(d.id)}
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
              <label className="text-xs text-zinc-500">Add domain</label>
              <input
                value={newDomain}
                onChange={(e) => setNewDomain(e.target.value)}
                placeholder="tamu.edu"
                className="mt-1 w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-md text-sm text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-600"
              />
            </div>
            <label className="flex items-center gap-1.5 text-xs text-zinc-400 pb-2">
              <input
                type="checkbox"
                checked={includeSub}
                onChange={(e) => setIncludeSub(e.target.checked)}
              />
              include subdomains
            </label>
            <button
              onClick={() => addDomain.mutate()}
              disabled={!newDomain.trim() || addDomain.isPending}
              className="px-3 py-2 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50"
            >
              Add
            </button>
          </div>
        </section>
      )}

      {/* Users in org */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-zinc-300">
          Users in this organization ({org.user_count})
        </h2>
        {users.length === 0 ? (
          <p className="text-sm text-zinc-500">No users.</p>
        ) : (
          <ul className="divide-y divide-zinc-800/50 border border-zinc-800 rounded-md">
            {users.map((u) => (
              <li
                key={u.id}
                className="px-3 py-2 flex items-center justify-between"
              >
                <span className="text-sm text-zinc-200">{u.email}</span>
                <span className="text-xs text-zinc-500">{u.name}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Danger zone (real orgs only) */}
      {!org.is_public && (
        <section className="space-y-2 border-t border-zinc-800 pt-4">
          <h2 className="text-sm font-semibold text-red-400">Danger zone</h2>
          <p className="text-sm text-zinc-500">
            Deleting an org un-assigns its users (they fall back to the public org
            on next sign-in) and removes it from every workspace's allow-list.
          </p>
          <button
            onClick={() => setShowDelete(true)}
            className="px-3 py-1.5 rounded text-xs font-medium bg-red-500/10 text-red-400 hover:bg-red-500/20 ring-1 ring-red-500/20"
          >
            Delete organization
          </button>
        </section>
      )}

      <ConfirmModal
        open={showDelete}
        onClose={() => setShowDelete(false)}
        onConfirm={() => remove.mutate()}
        title="Delete Organization"
        message={`This permanently deletes "${org.name}". Users on its domains fall back to the public org at next sign-in. Type the slug to confirm.`}
        confirmLabel="Delete Organization"
        danger
        isPending={remove.isPending}
        confirmInput={org.slug}
        confirmInputValue={deleteSlug}
        onConfirmInputChange={setDeleteSlug}
      />
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + lint**

Run: `cd /Users/sidx/workspace/identity-service/admin && npx tsc -b && npm run lint`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add admin/src/pages/OrganizationDetail.tsx
git commit -m "feat(admin-ui): organization detail — domains, toggle, users, delete

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Workspace "Access" tab

Adds an "Access" tab to `WorkspaceDetail.tsx` with the explicit "Restrict membership to specific organizations" switch (off = open) revealing an org checklist when on.

**Files:**
- Modify: `admin/src/pages/WorkspaceDetail.tsx`

- [ ] **Step 1: Add the tab to the TABS array**

Find `const TABS = ["Members", "Groups", "Roles"] as const;` and change it to:

```tsx
const TABS = ["Members", "Groups", "Roles", "Access"] as const;
```

- [ ] **Step 2: Render the tab**

Find the tab-content block (`{tab === "Roles" && <RolesTab workspaceId={id!} />}`) and add right after it:

```tsx
{tab === "Access" && <AccessTab workspaceId={id!} />}
```

- [ ] **Step 3: Add the imports**

Ensure these are imported at the top of `WorkspaceDetail.tsx` (some may already be present — do not duplicate):

```tsx
import { getOrganizations, getWorkspaceAllowedOrgs, setWorkspaceAllowedOrgs } from "../api/client";
```

(`useState`, `useQuery`, `useMutation`, `useQueryClient`, and `toast` are already imported in this file — confirm and reuse.)

- [ ] **Step 4: Add the `AccessTab` component** (append near the other tab components, e.g. after `RolesTab`)

```tsx
// Loader: waits for the current allow-list, then mounts the stateful inner
// component so its useState initializers seed from real data exactly once
// (no useEffect-sync or null-sentinel gymnastics).
function AccessTab({ workspaceId }: { workspaceId: string }) {
  const { data: allowed, isLoading } = useQuery({
    queryKey: ["workspace-allowed-orgs", workspaceId],
    queryFn: () => getWorkspaceAllowedOrgs(workspaceId),
  });
  if (isLoading || !allowed) {
    return <div className="h-32 bg-zinc-800/30 rounded-lg animate-pulse" />;
  }
  return (
    <AccessTabInner
      workspaceId={workspaceId}
      initialAllowed={allowed.organization_ids}
    />
  );
}

function AccessTabInner({
  workspaceId,
  initialAllowed,
}: {
  workspaceId: string;
  initialAllowed: string[];
}) {
  const queryClient = useQueryClient();
  const { data: orgs = [] } = useQuery({
    queryKey: ["organizations"],
    queryFn: getOrganizations,
  });

  const [restrict, setRestrict] = useState(initialAllowed.length > 0);
  const [selected, setSelected] = useState<Set<string>>(
    new Set(initialAllowed),
  );

  const toggleOrg = (orgId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(orgId)) next.delete(orgId);
      else next.add(orgId);
      return next;
    });
  };

  const save = useMutation({
    mutationFn: () =>
      setWorkspaceAllowedOrgs(
        workspaceId,
        restrict ? Array.from(selected) : [],
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace-allowed-orgs", workspaceId],
      });
      toast.success("Access updated");
    },
    onError: (e) => toast.error((e as Error).message),
  });

  return (
    <div className="space-y-4 max-w-xl">
      <div>
        <h3 className="text-sm font-semibold text-zinc-300">
          Organization access
        </h3>
        <p className="text-xs text-zinc-500">
          Restrict which organizations' users may be invited to this workspace.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={restrict}
          onChange={(e) => setRestrict(e.target.checked)}
        />
        <span className="font-medium">Restrict to specific organizations</span>
      </label>
      {!restrict && (
        <p className="text-xs text-zinc-500 -mt-2">
          Open — members from any organization may be invited.
        </p>
      )}

      {restrict && (
        <>
          <ul className="space-y-1 border border-zinc-800 rounded-md p-2">
            {orgs.map((o) => (
              <li key={o.id}>
                <label className="flex items-center gap-2 text-sm px-2 py-1">
                  <input
                    type="checkbox"
                    checked={selected.has(o.id)}
                    onChange={() => toggleOrg(o.id)}
                  />
                  {o.is_public && <span>🌐</span>}
                  {o.name}
                </label>
              </li>
            ))}
          </ul>
          {/* Guard the silent-open trap: restrict ON + nothing selected would
              PUT [] which the backend reads as "open" — block it. */}
          {selected.size === 0 && (
            <p className="text-xs text-amber-400 -mt-2">
              Select at least one organization, or turn off the restriction —
              saving with none selected leaves the workspace open to all.
            </p>
          )}
        </>
      )}

      <button
        onClick={() => save.mutate()}
        disabled={save.isPending || (restrict && selected.size === 0)}
        className="px-3 py-1.5 rounded text-xs font-medium bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50 transition-colors"
      >
        {save.isPending ? "Saving..." : "Save"}
      </button>
    </div>
  );
}
```

- [ ] **Step 5: Typecheck + lint**

Run: `cd /Users/sidx/workspace/identity-service/admin && npx tsc -b && npm run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add admin/src/pages/WorkspaceDetail.tsx
git commit -m "feat(admin-ui): workspace Access tab — restrict-to-orgs switch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Build gate + manual verification

**Files:** none (verification only).

- [ ] **Step 1: Full typecheck + lint + production build**

Run: `cd /Users/sidx/workspace/identity-service/admin && npx tsc -b && npm run lint && npm run build`
Expected: all succeed (the production `vite build` is the real gate that the app compiles end-to-end).

- [ ] **Step 2: Manual run-through against the live backend**

The backend (with the Plan-2a org endpoints) and dev DB are running (`make start` on :9003 if not). Start the admin UI: `cd /Users/sidx/workspace/identity-service && make admin` (:9004). Sign in as an admin, then verify each flow and confirm the UI + a DB/network check agree:

1. **Organizations nav** appears; the page lists the **public org pinned first** with a 🌐 badge and its enabled status.
2. **Create org** ("TAMU"/"tamu") → redirects to its detail page; it appears in the list.
3. **Add domain** "tamu.edu" with *include subdomains* checked → shows with a `+subdomains` badge; **Remove** deletes it.
4. **Disable** the org → its `StatusBadge` flips; the list reflects it.
5. On the **public org** detail: no Domains section, no Delete, and the toggle reads "Disable/Enable public sign-in".
6. **Delete** the TAMU org → type-the-slug confirm gating works; redirects to the list.
7. **Workspace → Access tab**: a workspace with no restriction shows the switch **off** ("Open"); flip it **on**, check an org, **Save**; reload the tab and confirm the state persisted; flip **off** + Save → back to open.
8. Trigger an error (e.g. add a duplicate domain) → a red `toast` shows the backend's 409 detail.

- [ ] **Step 3: Report verification results**

Note any flow that didn't behave as described. If all pass, Plan 2b is complete.

---

## Self-Review

**Spec coverage (admin design §6):**
- Sidebar "Organizations" + list page (public pinned, counts, New via Modal) → Task 2. ✓
- Org detail (enable toggle, domains editor with include-subdomains, users, type-to-confirm delete; public variant hides domains+delete, toggle = public sign-in) → Task 3. ✓
- Workspace "Access" tab (explicit Restrict switch, off=open, checklist, Save→PUT) → Task 4. ✓
- API client additions + React Query + sonner toasts → Tasks 1–4. ✓
- System Settings public mirror → **dropped (YAGNI, noted in header)**.
- Component tests → **replaced by typecheck + lint + manual run-through** (admin app has no test runner; noted in header).

**Placeholder scan:** none — every component is complete. The Task-2 `OrganizationDetail` stub is explicitly a temporary one-liner replaced in full by Task 3 (so the route compiles between tasks).

**Type consistency:** `Organization`/`OrgDomain`/`OrganizationDetail`/`OrgUser` (Task 1) are the exact types consumed by the pages (Tasks 2–4). Client signatures `createOrganization({name,slug})`, `updateOrganization(id,{name?,enabled?})`, `addOrgDomain(id,{domain,include_subdomains})`, `setWorkspaceAllowedOrgs(id, string[])` match every call site. `ConfirmModal`/`DataTable`/`Modal`/`StatusBadge` prop usage matches their real signatures from the reference section. `getOrganizations` is reused with the same `["organizations"]` query key across the list page and the Access tab (so creating/disabling an org refreshes the Access checklist too).
