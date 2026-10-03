# Organizations — Email-Domain Tenancy — Design

**Date:** 2026-06-09
**Status:** Approved (design)
**Branch:** `org-design`
**Task:** Add organizations keyed on email domain, gate sign-in by org, and let
workspaces restrict which orgs can be members.

## Problem

Today **anyone** with a verified IdP email can create a Duar account.
`find_or_create_user` (`service/src/services/auth_service.py:37`) applies no
allowlist or domain restriction; the only real gate is downstream — a user with
no workspace membership cannot obtain tokens (`issue_tokens`,
`auth_service.py:134`, raises if no `WorkspaceMembership`). There is no notion of
an **organization**: client apps receive `wid`/`wslug`/`wrole` in the token but
have no way to know *which company/tenant* a user belongs to.

We want:

1. **Restrict who can sign in** based on the email's domain (the `@`-part).
2. **Organizations** defined by one or more email domains (e.g. `tamu.edu` →
   "TAMU"). A user is automatically a member of the org that claims their domain.
3. The org **carried in the token** so client apps know the user's tenant.
4. A **public org** catch-all for personal accounts (gmail, etc.), with an
   admin-controlled **on/off** switch for public sign-in.
5. Orgs and their domains **managed in the admin app**.
6. Workspaces can **enforce which orgs** may be members.

## Model framing

An email has exactly one domain, so this is **one user → one org**, derived
deterministically at sign-in. Org membership is *resolved*, not invited. Org is an
**orthogonal membership guardrail** layered on top of the existing
workspace/role/ACL tiers — it does **not** own workspaces and does not change the
soft workspace-isolation model.

## Goals

- Resolve a signing-in user's org from their verified email domain; **reject**
  sign-in when no org claims the domain and the public org is disabled.
- Auto-assign org membership (no invite flow for orgs); re-resolve on every
  sign-in so domain/enable changes take effect at next login.
- Emit the org in user **and** service-to-service tokens.
- A **public org** singleton whose enable flag is the public sign-in switch.
- Per-workspace **allowed-orgs** restriction enforced both when adding members and
  when issuing tokens.
- Admin CRUD for orgs, domains, the public toggle, and workspace allowed-orgs.
- **Zero-lockout rollout**: existing users keep working on deploy.

## Non-Goals

- Org owning/partitioning workspaces (rejected: keeps soft-isolation model intact).
- Org-based auto-join of workspaces (a workspace lists *allowed* orgs; it does not
  auto-add members).
- Per-user org overrides / multi-org membership (one domain → one org).
- IdP-claim-based org assurance (Google `hd`, EntraID `tid`) — see Future work.
- A general DB-backed settings table (the public toggle lives on the org row).

## Decisions

| Decision | Choice |
|----------|--------|
| Workspace ↔ org | **Membership filter, manual invite.** Workspace optionally lists allowed orgs; empty list = open to all orgs (today's behavior). Org does not own workspaces. |
| Default posture | **Public ON by default.** Migration seeds the public org enabled and backfills every existing user into it. Nothing breaks on deploy. |
| Domain matching | **Exact + opt-in subdomains.** Each domain row has `include_subdomains`; resolution prefers the most specific (longest) match. |
| Membership storage | **Stored** `users.organization_id`, re-resolved every sign-in (not purely derived) — queryable and gives a stable claim. |
| Token claims | `oid` / `oslug` / `opub` in **both** the user access token and the authz (service-to-service) token. |
| Issuance enforcement | Token issuance is the **authoritative** gate: tightening a workspace's allowlist later can strand existing members (intended). |
| Disable-public semantics | Turning the public org off blocks **all** public-org users at their next sign-in, not just brand-new ones. |
| Public toggle home | The public org's `enabled` column (no separate settings table). |

## Design

### 1. Data model — 3 new tables + 1 column

**`organizations`** (`service/src/models/organization.py`, new)
- `id` UUID PK
- `slug` TEXT UNIQUE — e.g. `tamu`, `public`
- `name` TEXT NOT NULL
- `is_public` BOOLEAN NOT NULL default `false` — **exactly one** row may be true
  (partial unique index `WHERE is_public`)
- `enabled` BOOLEAN NOT NULL default `true` — for the public org this is the
  public sign-in switch; for a real org, `false` is a kill-switch blocking its users
- `created_by` UUID FK `users.id` SET NULL
- `created_at` TIMESTAMPTZ default now()

**`organization_domains`**
- `id` UUID PK
- `organization_id` UUID FK `organizations.id` CASCADE
- `domain` TEXT NOT NULL, normalized lowercase, **globally UNIQUE** (a domain
  cannot belong to two orgs — enforced at the DB level)
- `include_subdomains` BOOLEAN NOT NULL default `false`
- `created_at` TIMESTAMPTZ default now()

**`workspace_allowed_organizations`**
- `id` UUID PK
- `workspace_id` UUID FK `workspaces.id` CASCADE
- `organization_id` UUID FK `organizations.id` CASCADE
- UNIQUE(`workspace_id`, `organization_id`)
- **Zero rows for a workspace = open to all orgs** (preserves current behavior).

**`users.organization_id`** — new nullable UUID FK → `organizations.id`
(`service/src/models/user.py`). Nullable in schema; the app guarantees it is
populated on every sign-in and the migration backfills it. ON DELETE SET NULL.

Join tables (not JSON columns) are used deliberately so the DB enforces
domain-uniqueness and the one-public-org invariant.

### 2. Org resolution (`service/src/services/organization_service.py`, new)

`resolve_organization(db, email) -> Organization | None`:

1. **Normalize**: lowercase; take the substring after the **last** `@`; reject
   empty / multiple-`@` / malformed.
2. **Exact**: enabled org with a `organization_domains.domain == d` → return it.
3. **Subdomain**: among enabled orgs' domains where `include_subdomains = true`
   and (`d == domain` or `d` ends with `"." + domain`), return the **longest**
   (most specific) match.
4. **Public**: else if the public org exists and is `enabled` → public org.
5. Else **`None`** (sign-in not permitted).

### 3. Sign-in gate (`service/src/api/auth_routes.py` callback, `auth_service.py`)

In the OAuth callback, immediately after the existing `email_verified` check
(`auth_routes.py:199`) and before/within `find_or_create_user`:

- Call `resolve_organization`.
- `None` → **403, no user row created** for new users
  ("sign-in is not permitted for this email domain").
- Otherwise set/refresh `user.organization_id` (re-resolved on every sign-in, so
  claiming a domain or disabling an org takes effect at the next login).
- The rest of the flow (`is_active` check, workspace selection, token issuance) is
  unchanged.

Disabling the public org therefore blocks all public-org users at their next
sign-in, by the same mechanism.

The **AuthZ-mode** path (`POST /authz/resolve`, service-to-service JIT
provisioning) is a third sign-in path and applies the **same** gate: it resolves
the org from the validated IdP email and rejects (403) when `None`, so AuthZ mode
cannot be a side door around the domain restriction. The **admin** OAuth callback
is the deliberate exception — it resolves and persists the org but does not gate,
because admin access is already restricted to `is_admin` users and hard-gating
there could lock every admin out of the panel that configures orgs.

### 4. Token claims (`service/src/auth/jwt.py`)

`create_access_token` adds, next to `wid`/`wslug`:
- `oid` — org id (string)
- `oslug` — org slug
- `opub` — `is_public` boolean (lets client apps treat public-org users as untrusted)

The authz token builder (`jwt.py:90`) carries the same three claims for
service-to-service flows. Callers thread the resolved org through (it is already
on `user.organization_id`).

**SDKs**: surface `org` (id, slug, is_public) on the decoded-claims / user object —
Python SDK (`sdk/src/duar_auth/`) and JS SDKs (`@duar-auth/js` claim
types, react/nextjs re-exports). No verification-logic change; additive claims.

### 5. Workspace org-enforcement (two checkpoints)

- **Add member** (`workspace_service.invite_member`, `workspace_service.py:108`):
  if the workspace has any `workspace_allowed_organizations` rows, reject an
  invitee whose `organization_id` is not in the set (avoids creating dead
  memberships). No rows → allow.
- **Token issuance** (`auth_service.issue_tokens`, `auth_service.py:134`) — the
  **authoritative** gate: require an existing membership **AND** (the workspace has
  no allowed-org rows **OR** the user's org ∈ the allowed set). Tightening a
  workspace's allowlist later thus stops disallowed existing members from getting
  tokens at next issuance (intended enforcement; can strand prior members). The
  refresh path (`rotate_refresh_token`) and the **AuthZ mint** (`/authz/resolve`)
  apply the identical check before issuing a token, so no token-minting path
  bypasses the allowlist.

### 6. Admin surface

New admin endpoints (alongside the existing admin routes in
`service/src/api/admin_routes.py`, or a dedicated `org_routes.py`), admin-cookie
auth, rate-limited and audit-logged like existing admin mutations:

- **Orgs**: list / create / update / delete; toggle `enabled`.
- **Domains**: add / remove a domain on an org (with `include_subdomains`); **409**
  on a domain already claimed by another org.
- **Public org**: surfaced as a special, non-deletable row; its `enabled` toggle is
  the public sign-in switch (read view also exposed in `/admin/system/settings`).
- **Workspace allowed-orgs**: list / set the orgs permitted for a workspace
  (multi-select).
- **Read**: list users in an org.

Admin React panel mirrors these: an Organizations page (org list, domain editor,
public toggle) and an allowed-orgs control in WorkspaceDetail.

### 7. Migration / rollout (`service/migrations/versions/`)

Autogenerate from the new models (latest revision today:
`21bddf454fbc_initial_schema.py`), then hand-edit the data step:

1. Create the three tables + `users.organization_id` column + indexes
   (partial-unique on `is_public`, unique on `organization_domains.domain`).
2. **Seed** the public org: `slug='public'`, `name='Public'`, `is_public=true`,
   `enabled=true`.
3. **Backfill**: for every existing user, set `organization_id` via
   `resolve_organization` — with no real orgs yet, all land in the public org.
4. Downgrade drops the column and tables.

Result: zero downtime, no lockouts — matches "public ON by default."

## Testing

- `resolve_organization`: exact match wins; subdomain match only when
  `include_subdomains`; longest/most-specific subdomain wins; disabled org is
  skipped; unclaimed domain → public when enabled; `None` when public disabled;
  malformed email rejected; case-insensitive.
- Sign-in gate: new user from unclaimed domain with public disabled → **403, no
  user row**; matched domain → user created with correct `organization_id`;
  `organization_id` **re-resolved/updated** on a later sign-in after a domain is claimed.
- Token claims: `oid`/`oslug`/`opub` present and correct on access and authz
  tokens; public-org user has `opub=true`.
- Workspace enforcement: invite blocked for a disallowed org; allowed when no
  restriction; issuance blocked after the allowlist is tightened around an existing
  member; issuance allowed when org ∈ set.
- DB invariants: second org cannot claim an already-used domain (unique violation);
  a second `is_public=true` org is rejected (partial-unique violation).
- Migration: public org seeded; all pre-existing users backfilled into it.
- SDK: decoded claims expose `org`; existing flows unaffected by the additive claims.

## Security considerations

- **Trust basis**: org assignment is only as trustworthy as the IdP's verified
  email. The existing `email_verified` gate (`auth_routes.py:199`) is a
  prerequisite and stays.
- **Domain normalization** must be strict: lowercase, single `@`, reject empty,
  and punycode/IDN-normalize to prevent homoglyph/encoding spoof matches.
- **Cross-org leak**: global domain-uniqueness plus the existing workspace
  grantee-validation keep a user's org unambiguous and membership constrained.

## Future work (deferred)

- **Authoritative org assurance** per IdP: prefer Google Workspace `hd` and
  EntraID `tid` over email-string parsing for those providers.
- **Trust tiers** for public-org users beyond the `opub` flag (e.g. limited scopes)
  if a real need appears.
- **Org-scoped admins / org settings** (branding, default workspace) — a larger
  feature; out of scope here.
