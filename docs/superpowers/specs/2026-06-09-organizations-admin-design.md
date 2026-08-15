# Organizations Admin Surface (Phase 2) — Design

**Date:** 2026-06-09
**Status:** Approved (design)
**Branch:** `org-design`
**Builds on:** `2026-06-09-organizations-email-domain-tenancy-design.md` (Plan 1 — backend engine, already implemented on this branch).

## Problem

Plan 1 built the organizations engine (models, resolution, sign-in gate, token
claims, workspace allowed-orgs enforcement, migration). But there is **no way to
manage any of it**: organizations, their email domains, the public-sign-in
toggle, and per-workspace allowed-orgs are all created only by direct DB writes /
the seed migration. Admins need a UI (and the API behind it) to operate the
feature. This is Phase 2 — the admin surface.

## Goals

- Admin **CRUD for organizations** (create, list, view, rename, enable/disable,
  delete) and their **email domains** (add/remove, with `include_subdomains`).
- Operate the **public org** as a special, non-deletable row whose `enabled`
  flag is the **public-sign-in toggle**.
- Per-workspace **allowed-orgs** management with an unambiguous open-vs-restricted
  control.
- Everything follows existing admin conventions (`require_admin` + CSRF, rate
  limits where warranted, activity logging) and existing React admin patterns.

## Non-Goals

- Changing any Plan-1 engine behavior (resolution, gate, claims, enforcement).
- Org-scoped admins / org settings (branding, default workspace) — future.
- Bulk domain import, org merge, or moving users between orgs.
- Self-service (non-admin) org management.
- The IdP-claim org-assurance hardening (Google `hd` / EntraID `tid`) — still
  deferred from Plan 1.

## Decisions

| Decision | Choice |
|----------|--------|
| Org admin layout | **List + detail pages** (mirrors Service Apps), not a single expandable-row page. |
| Allowed-orgs control | **Explicit "Restrict membership to specific organizations" switch** (off = open) revealing an org checklist when on — not a bare chips multiselect. |
| Plan split | **API first (Plan 2a), React UI second (Plan 2b).** |
| Service module | New **`org_admin_service.py`**, separate from the hot-path `organization_service.py`. |
| slug mutability | **Immutable after create** (it is emitted in the `oslug` token claim). |
| Allowed-orgs write | **Replace-the-whole-set** `PUT` (`{organization_ids: [...]}`), empty = open. |
| Users-in-org | A **paginated** `GET .../users` reusing the admin user-list shape. |
| Workspace tab label | **"Access"**. |

## Design

### 1. Sequencing

Two implementation plans from this one spec:
- **Plan 2a — Admin API**: `org_admin_service.py`, schemas, routes, tests. TDD
  with fake sessions + `dependency_overrides` route tests (Plan-1 style).
- **Plan 2b — React admin UI**: Organizations list + detail pages, the Access tab
  on Workspace detail, the System-Settings mirror. Consumes 2a.

### 2. Backend API

A new **`service/src/api/org_admin_routes.py`** module (admin_routes.py is
already ~1400 lines — keep the org surface isolated). It defines its own
`APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])`
— identical setup to the existing admin router (admin cookie + `X-Requested-With`
CSRF) — and is registered in `service/src/main.py`. All mutations call
`activity_service.log_activity(...)` then `await db.commit()`.

| Method & path | Purpose |
|---|---|
| `GET /admin/organizations` | List: `id, name, slug, is_public, enabled, domain_count, user_count` |
| `POST /admin/organizations` | Create `{name, slug}` → 201; `is_public=false` always |
| `GET /admin/organizations/{id}` | Detail incl. `domains[]` and `user_count` |
| `PATCH /admin/organizations/{id}` | Update `name?` and/or `enabled?` (slug NOT updatable) |
| `DELETE /admin/organizations/{id}` | Delete (204) — guarded against the public org |
| `POST /admin/organizations/{id}/domains` | Add `{domain, include_subdomains}` → 201 |
| `DELETE /admin/organizations/{id}/domains/{domain_id}` | Remove (204) |
| `GET /admin/organizations/{id}/users` | Paginated users in the org |
| `GET /admin/workspaces/{id}/allowed-organizations` | Current allowed org set |
| `PUT /admin/workspaces/{id}/allowed-organizations` | Replace set with `{organization_ids: [...]}` (empty = open) |

Rate-limit `POST /admin/organizations` and the domain-add endpoint (mirroring the
`@limiter.limit("5/minute")` on service-app create).

### 3. Service layer — `service/src/services/org_admin_service.py` (new)

Functions (all `async`, take `db`): `list_organizations`, `create_organization`,
`get_organization_detail`, `update_organization`, `delete_organization`,
`add_domain`, `remove_domain`, `list_org_users`, `get_workspace_allowed_orgs`,
`set_workspace_allowed_orgs`. Reuses `organization_service.normalize_domain` for
domain validation. Raises `ValueError` for guard violations (routes map to
4xx, consistent with existing admin routes); raises a dedicated conflict for
duplicate slug/domain → 409.

`organization_service.py` (hot path) is untouched except possibly exposing a
shared constant for the public-org duar.

### 4. Schemas — `service/src/schemas/admin.py` (extend)

Request/response models following the file's existing pattern (`SafeStr`,
`from_attributes`): `AdminOrgCreateRequest` (`name: SafeStr`,
`slug: pattern ^[a-z0-9][a-z0-9-]*[a-z0-9]$`), `AdminOrgUpdateRequest`
(`name?`, `enabled?`), `AdminOrgResponse`, `AdminOrgDetailResponse`
(+`domains`, `user_count`), `AdminOrgDomainCreateRequest`
(`domain: str`, `include_subdomains: bool = False`), `AdminOrgDomainResponse`,
`AdminWorkspaceAllowedOrgsRequest` (`organization_ids: list[UUID]`),
`AdminWorkspaceAllowedOrgsResponse`.

### 5. Validation & guards

- **slug**: pattern-validated, unique → 409.
- **name**: `SafeStr`.
- **domain**: `normalize_domain` (rejects malformed; null-byte/length/IDN); the
  *normalized* value is stored; globally unique → 409.
- **Public org is special**: guard violations raise `ValueError` → **400**
  (consistent with the existing admin routes' `ValueError`→400 mapping). `DELETE`
  on it → 400; cannot be created (route never sets `is_public`; DB partial unique
  index also enforces a single public org); `POST .../domains` on it → 400 (it is
  the catch-all, domains don't apply); `PATCH enabled` is allowed (the toggle).
- **Allowed-orgs PUT**: validates every `organization_id` exists; replaces the
  set transactionally.

### 6. Admin UI (Plan 2b)

- **Sidebar**: add "Organizations" to the `NAV` array (`components/Layout.tsx`).
- **Organizations list** (`pages/Organizations.tsx`): `DataTable` of orgs; the
  public org pinned on top with a 🌐 badge and its on/off state; columns
  name/slug/domains/enabled/users; "New org" via `Modal`. Row → detail route.
- **Org detail** (`pages/OrganizationDetail.tsx`, mirrors `ServiceAppDetail`):
  header with name + **enable toggle**; **Domains** section (rows with a
  `+subdomains` badge and remove button; add-domain form with an "include
  subdomains" checkbox); **Users in org** (count + list); **Danger zone** delete
  via `ConfirmModal` with type-the-slug-to-confirm. The **public org** variant
  hides Domains + Delete and foregrounds the **Public sign-in** toggle.
- **Workspace detail → "Access" tab** (`pages/WorkspaceDetail.tsx`, extend
  `TABS`): the explicit **Restrict membership to specific organizations** switch;
  off = "any organization's users may be invited"; on reveals an org checklist;
  Save calls `PUT .../allowed-organizations`.
- **System Settings**: a read-only line mirroring the public-sign-in state.
- API client additions in `admin/src/api/client.ts` (org CRUD, domains,
  allowed-orgs), React Query `useQuery`/`useMutation` with `invalidateQueries`
  and `sonner` toasts, matching existing pages.

### 7. Audit logging

`log_activity` for: `org_create`, `org_update`, `org_delete`,
`org_domain_add`, `org_domain_remove`, `org_public_toggle` (a distinct action so
public-sign-in changes are easy to audit), and `workspace_allowed_orgs_set`
(with before/after org ids in `detail`).

## Testing

**Plan 2a (backend):**
- `org_admin_service` unit tests (fake sessions): create/list/update/delete;
  public-org guards (no delete, no domains); slug + domain 409s; domain
  normalization on add; allowed-orgs replace semantics (incl. empty = open).
- Route tests (`dependency_overrides`, `test_authz_resolve_guard` style): the
  public-org delete/domain guards return 4xx; CSRF/`require_admin` enforced;
  `PUT` allowed-orgs validates membership of org ids.

**Plan 2b (frontend):**
- Org list renders incl. pinned public row; create modal posts; domain add/remove
  mutations; the Restrict switch toggles between open and restricted states and
  Save sends the right payload; type-to-confirm delete is gated.

## Affected files

**Create:** `service/src/services/org_admin_service.py`;
`service/src/api/org_admin_routes.py` (new router, registered in `main.py`);
`admin/src/pages/Organizations.tsx`, `admin/src/pages/OrganizationDetail.tsx`;
tests under `service/tests/` + `admin/`.
**Modify:** `service/src/schemas/admin.py`, `service/src/main.py` (router, if a
new module), `admin/src/api/client.ts`, `admin/src/components/Layout.tsx`,
`admin/src/pages/WorkspaceDetail.tsx`, the System Settings page.
