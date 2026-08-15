# Changelog

All notable changes to Duar (service, Python SDK, JS SDKs) are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Check the `Breaking changes` section before upgrading.

For versions prior to `0.11.0`, see the git tag history (`git log --oneline -- service/ sdk/ sdks/`).

## [Unreleased]

<!-- Add next-version entries here -->

---

## [1.0.0] - 2026-08-15 — Renamed to Duar

First release under the new name. Functionally identical to 0.20.1 apart from
the rename below; version reset to 1.0.0 because every package, image and import
path is new.

### Breaking changes — project renamed Sentinel → **Duar**

The product, packages, images and SDK APIs are renamed. Nothing about the wire
protocol, database schema or Redis layout changed; this is a rename, not a
migration. Consumers upgrade by search-and-replace:

| Was | Now |
|---|---|
| PyPI `sentinel-auth-sdk` / `import sentinel_auth` | `duar-auth` / `import duar_auth` |
| npm `@sentinel-auth/{js,react,nextjs}` | `@duar-auth/{js,react,nextjs}` |
| `ghcr.io/sidxz/sentinel`, `ghcr.io/sidxz/sentinel-admin` | `ghcr.io/sidxz/duar`, `ghcr.io/sidxz/duar-admin` |
| Python `Sentinel`, `SentinelError` | `Duar`, `DuarError` |
| JS `SentinelAuth`, `SentinelAuthz`, `SentinelAuthProvider`, `createSentinelMiddleware`, `createSentinelAuthzMiddleware`, `createSentinelProxy`, `Sentinel*Config`, `SentinelUser` | `DuarAuth`, `DuarAuthz`, `DuarAuthProvider`, `createDuarMiddleware`, `createDuarAuthzMiddleware`, `createDuarProxy`, `Duar*Config`, `DuarUser` |
| JS option `sentinelUrl` / `sentinelJwksUrl` | `duarUrl` / `duarJwksUrl` |
| Env vars `SENTINEL_URL`, `SENTINEL_SERVICE_KEY`, `SENTINEL_BACKEND` (admin image), `SENTINEL_PORT`, `VITE_/NEXT_PUBLIC_SENTINEL_URL` | `DUAR_*` |
| Browser storage / cookie keys `sentinel_*` | `duar_*` (users are signed out once on upgrade) |
| Repo `github.com/sidxz/Sentinel` | `github.com/sidxz/duar` |

JWT `aud` values are renamed too: `sentinel:{access,refresh,admin,authz,m2m}` →
`duar:{access,refresh,admin,authz,m2m}`. Tokens issued by a pre-rename service
are rejected by the renamed SDKs and vice-versa — upgrade service and SDKs
together; existing sessions re-authenticate once.

---

---

## [0.20.1] - 2026-08-14 — Entra ID sign-in + logging restoration

Entra ID had never actually worked: its tokens carry no `email_verified` claim,
so the strict verification gate rejected every one of them, on every path. Fixed
and verified end to end against a live tenant. Separately, in-process startup
migrations were silently dismantling the service's structured logging on every
boot — JSON rendering and all `info`-level events, including every successful
access-log record, were lost for the life of the process.

### Breaking changes

None. (JS/Python SDKs are republished unchanged for version alignment.)

### Fixed
- **Service:** Microsoft Entra ID sign-in was rejected on every path (`/authz/resolve`, app login, admin login). Entra emits no `email_verified` claim, so the strict `email_verified is True` gate failed closed for all Entra tokens. Claims from the tenant pinned by `ENTRA_TENANT_ID` are now accepted as tenant-verified unless `xms_edov` is explicitly `false`; every other issuer still requires `email_verified: true`. The exemption is keyed on the provider the token was *verified against*, never on a `tid` claim, so no other trusted issuer can inherit it. `validate_idp_token` no longer returns an `email_verified` field at all — verification is a gate, not a payload.
- **Service:** startup migrations silently destroyed the app's logging configuration. `migrations/env.py` calls `fileConfig(alembic.ini)`, which replaces the root handlers `configure_logging()` had just installed and resets the root level to alembic's `WARN`. Since `main.py` migrates in-process on boot, every deployment lost JSON log rendering and dropped all `info`-level events — including every 2xx `http.access` record — for the life of the process. The app now opts out via `config.attributes["configure_logger"]`; CLI `alembic` runs keep their own logging.
- **Service:** Tokens without an `email` claim raised `KeyError` → 500. Entra omits `email` for managed work accounts unless the app registration adds it as an optional claim, so Duar now falls back to an address-shaped `preferred_username`/`upn` and otherwise refuses sign-in with an actionable message (`no_email_claim`). Identity remains keyed on `sub`.
- **Admin/API:** `GET /admin/system/settings` reported the Entra provider as `entra`; the identifier is `entra_id` everywhere else (callback paths, `/auth/providers`, `provider` on `/authz/resolve`). Docs listed the admin callback as `{BASE_URL}/auth/callback/entra` — corrected to `entra_id`.

### Security note

For Entra, `ENTRA_TENANT_ID` pinning replaces the missing `email_verified`
assertion: a token from that one tenant is treated as carrying a tenant-verified
address. Enable the `xms_edov` optional claim on the app registration to get an
explicit domain-ownership assertion instead — Duar already rejects
`xms_edov: false`. This matters because an email address drives organization
resolution and `ADMIN_EMAILS` auto-promotion (which fires only at first
provisioning of an identity, never on subsequent sign-ins). Google and GitHub
are unaffected: both attest the address and are still held to that.

---

## [0.20.0] - 2026-08-10

### Added
- **SDK:** `AuthenticatedUser`/`RequestAuth` now expose `org_id`, `org_slug`, `org_is_public`, parsed from the `oid`/`oslug`/`opub` claims in both access and authz tokens (previously decoded and discarded). Absent claims yield `None`/`False` — no breaking change.
- **Service:** `GET /organizations` on the internal (service-key) listener — enabled-org directory for client apps (`?include_disabled=1` for all).

---

## [0.19.0] – 2026-07-29 — Tier-1 security signals

Detect-only anomaly detection over the auth surface (the Tier 1 rules from
the AI-security roadmap): impossible travel, new-country / new-device
first-seen flags, and credential-stuffing detection — pure arithmetic over
events Duar already records, no ML, no model, and never in the
authorization decision path. Every rule is fail-open: a telemetry failure can
never break or block an auth flow.

### Breaking changes

None. (JS/Python SDKs are republished unchanged for version alignment.)

### Service

- **Added** security-signal engine (`signal_service`) evaluated on login
  success, refresh-context change, and login failure:
  - `login_impossible_travel` (high) — country change at an implied speed
    above `SIGNAL_IMPOSSIBLE_TRAVEL_KMH` (default 900), computed via a static
    country-centroid haversine; per-country-pair damper suppresses VPN
    ping-pong repeats for 6 h.
  - `login_new_country` (medium) / `login_new_device` (low) — first-seen
    flags per user (device = browser/OS family, so version bumps don't fire);
    a user's first login seeds silently.
  - `credential_stuffing_suspected` (high) — per-IP failure counters:
    ≥ `SIGNAL_STUFFING_FAILURES` (10) failures **and**
    ≥ `SIGNAL_STUFFING_DISTINCT_EMAILS` (5) distinct emails within
    `SIGNAL_STUFFING_WINDOW_MINUTES` (15), one signal per window.
  - Signals are ordinary activity rows plus `auth.signal.*` security-stream
    events (new `anomaly` outcome; high severity streams at warning level).
    Master switch: `SIGNALS_ENABLED` (default on). Detect-only — no
    enforcement, no blocking, no auto-revocation.
- **Added** audit rows for login START rejects (unknown provider, non-S256
  PKCE, unregistered redirect_uri, admin provider) — previously stream-only
  or silent, invisible to the admin panel. These config-shaped rejects do not
  feed stuffing counters. All login failure/reject stream events now carry
  `source_ip`.
- **Fixed** `POST /admin/service-apps` never refreshed the in-memory CORS
  origin set — a service app created with `allowed_origins` in one shot had a
  correct DB row, but its origins stayed dark until a restart or an unrelated
  app edit rebuilt the set. Now refreshes on create, matching the other four
  origin-affecting routes.

### Admin panel

- **Added** Security signals card on the Dashboard — per-signal counts over
  the last 30 days, deep-linking to the Activity page pre-filtered to that
  signal (`/activity?action=…` is now honored as an initial filter).
- **Added** the four signal actions to the Activity filter dropdown and the
  "Auth anomalies" dashboard chart bucket.

---

## [0.18.0] – 2026-07-28 — Usage analytics, log coverage, private-network deployment

Three strands: an action-usage analytics surface (admin Usage dashboard backed
by a new insights endpoint and a daily usage rollup), a log-coverage audit that
instruments the token and authorization surfaces end to end, and reverse-proxy
SDK helpers so Duar can run with no browser-reachable address
(ClusterIP-only / internal overlay network).

### Breaking changes

None.

### Service

- **Added** `GET /admin/actions/insights` — action-usage analytics for the
  admin panel: per-action/per-service usage aggregates over a new
  `action_usage` daily rollup, allowed-vs-denied trends, and dormant-grant /
  unused-role mining (grants and roles with no recorded use).
- **Added** RBAC check verdict recording: allowed checks land in the
  `action_usage` rollup, denials become `action_denied` activity events.
- **Added** security events across the token surface (issue, refresh, revoke,
  denylist hits), entity-ACL verdict audits, owner-path workspace/group
  mutation audits, guard-denial logging, and logout symmetry (log-coverage
  tranches A–C).
- **Added** tier-1 activity enrichment: login-failure audit events and
  refresh-context (IP / user-agent) on token refresh.
- **Fixed** `GET /admin/actions/insights` query-string coercion (pydantic
  `Literal` query params 422'd on valid values); regression test added.

### Admin panel

- **Added** Usage dashboard — action analytics (usage trends, top actions,
  allowed/denied split) plus dormant-grant and unused-role mining, with a
  per-workspace Usage tab in workspace detail.
- **Fixed** Activity page event rendering gaps surfaced by the tier-1
  enrichment; new Insights page for login/refresh context.

### Python SDK

- **Added** `Duar.proxy_router()` / `create_proxy_router()` — a FastAPI
  reverse-proxy router for private-network deployments where browsers cannot
  reach Duar. Forwards only the browser-facing surface: `POST
  /authz/resolve` (service key injected — doubles as the mint endpoint) and the
  read-only directory endpoints (members, groups, group members, `/users/me`)
  with the caller's tokens passed through. `X-Forwarded-For`/`User-Agent` are
  forwarded so Duar's access logs and rate limits still see real client IPs
  (pair with `BEHIND_PROXY`/`TRUSTED_PROXY_COUNT` on the service).

### JS SDKs

- **Added** `@duar-auth/nextjs/proxy` — `createDuarProxy()` route-handler
  factory for `app/api/duar/[...path]/route.ts`, implementing the same
  allowlist/key-injection rules as the Python proxy router.
- **Documented** that `DuarAuthzConfig.duarUrl` may be a same-origin
  path (e.g. `"/api/duar"`) pointing at one of the reverse-proxy helpers,
  with `mintEndpoint: "/api/duar/authz/resolve"`; regression tests lock in
  that all browser calls stay same-origin in that configuration.

### Docs

- **Added** `deployment/private-network.md` — running Duar ClusterIP-only
  in Kubernetes / Docker Swarm: proxy wiring, client-IP preservation, and the
  GitHub-IdP / admin-access / rate-limit caveats.

---

## [0.17.2] – 2026-07-20 — CORS preflight allows X-Authz-Token

Patch for browser authz-mode: the JS SDK's member-directory and share-dialog
helpers (`searchMembers`, `listGroups`, `getGroupMembers`, `getProfile`) send
`X-Authz-Token` cross-origin, but the header was missing from the CORS
`allow_headers` list — the preflight returned 400 "Disallowed CORS headers"
and the fetch threw before the request ever reached the endpoint. The
server-side accept path shipped in 0.15.0; the CORS config lagged behind it.

### Breaking changes

None. Service only — SDK and admin packages republished for version alignment.

### Service

- **Fixed** `X-Authz-Token` added to CORS `allow_headers`, so cross-origin
  browser calls in authz mode (member directory, groups, profile) pass
  preflight. New regression test asserts the preflight accepts every header
  the browser SDKs send.

---

## [0.17.1] – 2026-07-18 — /health exempt from Host validation

Patch for Kubernetes deployments: liveness/readiness probes hit the pod IP
directly, so their Host header is never on the `ALLOWED_HOSTS` allowlist —
the probe got a 400 from `TrustedHostMiddleware`, failed repeatedly, and
kubelet killed an otherwise-healthy pod ~90 seconds after startup.

### Breaking changes

None. Service only — SDK and admin packages republished for version alignment.

### Service

- **Fixed** `/health` is now exempt from TrustedHost (Host header) validation.
  The endpoint returns a static `{"status": "ok"}` with no version or
  dependency detail, so the check added nothing there. All other routes remain
  host-validated. No config changes needed; k8s probes work without a custom
  `Host` header.

---

## [0.17.0] – 2026-07-12 — Admin picker dialogs

The admin panel's five "add X" pickers (workspace members, role actions, role
members, role groups, group members) are replaced by one shared searchable
multi-select dialog. The workspace Members flow is renamed from "Invite
Member" to "Add Users" — Duar has no invitation mechanism; users always
come from IdPs, and the backend has only ever attached existing users.

### Breaking changes

None. Admin SPA only — no service, API, or SDK changes (service and SDK
packages republished for version alignment).

### Admin

- **Added** `AddItemsDialog`, a shared picker: search box, grouped checkbox
  list (role actions group by service with per-service "select all"),
  disabled rows with reasons ("already a member" / "already assigned"), and
  a single "Add N …" batch commit. Selections survive across searches in
  server-search mode.
- **Changed** Members tab to "+ Add Users": server-side search over existing
  users (name/email), multi-select, one batch role applied to all (default
  `viewer`; per-member roles remain editable inline afterward).
- **Changed** role-action adds to one batched `addRoleActions` request;
  member/group adds fan out per item and report partial failures in a single
  summary toast (org-restriction rejections and duplicate races surface
  per user).
- **Removed** all user-facing "invite" wording (the backend route
  `/admin/workspaces/{id}/members/invite` and API client names are
  intentionally unchanged).

---

## [0.16.0] – 2026-07-12 — Groups as role assignees

Workspace groups can now be assigned to custom RBAC roles: every member of a
bound group holds the role's actions for as long as they are in the group.
Binding a group to a role is admin-panel-only; workspace admins control who is
in a bound group, never what the group can do (mirrors how group shares already
work in entity ACLs). Group-derived grants resolve live at check time and flow
into authz-token `actions` claims with no client-side changes.

### Breaking changes

None. The API surface is additive; existing SDKs are fully compatible.

### Service

- **Added** `group_roles` table (migration `c8e1a4f7d3b9`, auto-applies on
  startup) mirroring `user_roles` — CASCADE FKs mean group/role deletion and
  workspace-member removal clean up bindings with no extra purge logic.
- **Changed** `check_action` / `get_user_actions` to resolve a UNION of direct
  (`user_roles`) and group-derived (`group_roles` ⋈ `group_memberships`)
  grants. Endpoint contracts (`/roles/check-action`, `/roles/user-actions`)
  are unchanged in shape and semantics.
- **Added** admin endpoints `GET/POST/DELETE /admin/roles/{role_id}/groups[/{group_id}]`
  with `role_group_added` / `role_group_removed` activity events;
  `RoleResponse` gains `group_count`.
- **Added** activity events for user-facing group mutations
  (`group_created`, `group_deleted`, `group_member_added`,
  `group_member_removed`) — previously unaudited, and now a privilege-grant
  path.

### Admin panel

- **Added** Groups section in the role detail row (bind/unbind groups, member
  counts) and group counts in the roles list.
- **Fixed** stale picker state when switching expanded role rows — a selection
  made on one role could silently apply to another role's add action/member/
  group picker.

### SDKs (Python + JS/React/Next.js)

- No code changes — published at 0.16.0 for version alignment only. No
  upgrade required.

---

## [0.15.0] – 2026-07-10 — Security hardening + auth-contract fixes

A correctness/security round across the service and SDKs: closes an auth-code
information-disclosure gap, hardens Redis TLS and cross-tenant cleanup, and fixes
several broken auth flows (proxy-mode React login, authz-mode profile/share
calls, Next.js non-ASCII display names). Validated end-to-end against the
Layer-2 live pentest (tenant isolation and auth-token integrity both proven).

### Breaking changes

- **`POST /auth/workspaces` + PKCE verifier** (was `GET /auth/workspaces?code=`). The
  auth code travels in the redirect URL, so possession of a leaked code alone must not
  disclose the victim's workspace names/slugs/roles — the endpoint now requires the same
  `code_verifier` proof `POST /auth/token` demands. `@duar-auth/js` `getWorkspaces()`
  is updated in lockstep. **Upgrade the server and `@duar-auth/js` together.**

### Security

- **`/auth/workspaces` information disclosure** — see Breaking changes; a non-consuming
  peek with no PKCE let any holder of a leaked auth code enumerate a victim's workspaces
  and roles.
- **Redis TLS fails closed in production** — a `rediss://` deployment with
  `REDIS_TLS_VERIFY` unset (default `none`) previously only warned and accepted any
  certificate (MITM). Startup now refuses to boot with `DEBUG=False` unless
  `REDIS_TLS_VERIFY=required`.
- **`remove_member` purges entity-ACL shares** — removing a user from a workspace left
  their per-resource `resource_shares` behind, silently reinstating old access if they
  were re-invited. Now cleaned up alongside group memberships and RBAC roles.
- **`delete_group` purges group shares and revokes member tokens** — deleting a group
  left its `resource_shares` orphaned and members' group-derived access live until token
  expiry.
- **Realm slug ↔ service_name uniqueness** — realm slugs and standalone service names
  share the authz `svc` scope namespace; creation now rejects a collision at both paths so
  a token minted for one trust domain can't be honored by the other.
- **`assign_user_role` requires current workspace membership** — a role could be assigned
  to a non-member and silently activate if that user was later invited.
- **GitHub IdP audience binding fails closed** — when an app configures
  `allowed_idp_audiences`, GitHub tokens (opaque, un-bindable) are now rejected instead of
  silently skipping the per-app binding check.

### Fixed

- **Proxy-mode React login** (`@duar-auth/js`, `react`) — the SPA sends an OAuth
  `state` parameter the server never echoed, so `verifyCallbackState()` always threw and
  every packaged proxy-mode login failed. The server now round-trips `state` on the
  callback redirect.
- **AuthZ-mode profile / share-dialog calls** — the SDK's browser helpers send the authz
  token in `X-Authz-Token`, which the server never read (`get_current_user_flexible` only
  accepted a Duar access token), so every such call `401`'d. The server now
  authenticates the `X-Authz-Token`.
- **Next.js middleware crash on non-Latin-1 display names** (`@duar-auth/nextjs`) — a
  display name with a code point > 255 (CJK, Cyrillic, …) threw when written to a request
  header and was misclassified as an auth failure, looping the user through login. Names
  are now percent-encoded on write and decoded on read.
- **`share_resource` 500 on re-share** — re-sharing a resource with the same grantee (e.g.
  a view→edit upgrade) violated `uq_resource_share` and surfaced as HTTP 500; it is now an
  atomic upsert.
- **`@duar-auth/js` `getProviders()`** — unwraps the server's
  `{ "providers": [...] }` response instead of returning the object as an array.
- **IdP JWKS refetch on unknown key** — a token signed by an IdP key rotated in after the
  cached JWKS snapshot failed until the 1-hour TTL lapsed; the validator now refetches
  once (rate-limited) before rejecting.
- **CSV user import isolation** — a DB-level error on one row poisoned the async session so
  every later row failed and the whole import 500'd; each row now runs in a savepoint.
- **Multi-tab logout on token refresh** (`@duar-auth/js`) — with the same origin open in
  several tabs, they refreshed at the same instant using the same rotating refresh token; the
  server correctly saw the second use as reuse and revoked the whole session, logging every tab
  out. The SDK now serializes refresh across tabs with the **Web Locks API** (single-flight), and a
  tab that finds the token already rotated picks up the new one instead of replaying the consumed
  one. Degrades safely when Web Locks is unavailable (runs unlocked, guarded by the re-read check),
  fails soft rather than replaying the token if the lock can't be acquired in time, staggers refresh
  timers with jitter, and uses a `BroadcastChannel` so logout / refresh propagate across tabs
  immediately. The server stays strict on reuse detection — the fix is entirely client-side.

---

## [0.14.1] – 2026-06-30

### Fixed

- **`duar-auth` (Python)** — `Duar.fetch_whoami` tolerates a non-JSON `200`
  from `/realm/whoami` (catches `ValueError` / JSON decode errors and degrades to
  standalone), so a misrouted `/realm/whoami` serving the admin SPA can no longer crash a
  downstream authz service on startup.

---

## [0.14.0] – 2026-06-29 — Realms (trusted app groups) + admin UI re-skin

### Added

- **Realms — trusted app groups + no-user (m2m) authz** — a shared authz scope across a
  group of apps, plus machine-to-machine tokens with no user context: `duar:m2m`
  tokens, `/realm/whoami` and `/realm/m2m-token` endpoints, admin Realms CRUD + membership
  UI, and Python/JS SDK helpers (`SystemAuth`, `mint_m2m_token`, `verify_m2m_token`,
  `require_system`).
- **Service tiers** — `create_app(tier)` splits apps into public / internal / all.

### Changed

- **Admin panel rebuilt** on shadcn/ui with light/dark theme tokens and IBM Plex.

---

## [0.13.1] – 2026-06-20

### Fixed

- **Access-log `http.route`** — starlette 1.x renamed `route.path` → `path_format`, so the
  access-log middleware logged `__unmatched__` for every request; it now prefers
  `scope["route"]` / `path_format` and records the real route template.

### Changed

- **Dependency upgrade** clearing all lockfile HIGH advisories (starlette 0.52 → 1.3,
  redis 7 → 8, structlog 25 → 26, cryptography 46 → 49, fastapi → 0.138, and others).

---

## [0.13.0] – 2026-06-17 — Structured logging + AuthZ session robustness

Commercial-grade structured JSON logging across the service and admin SPA (an
AI-ready envelope for anomaly detection), per-app IdP audience binding, and a fix
for the AuthZ JS "zombie session" after reload.

### Fixed

- **AuthZ "zombie session" after page reload** (`@duar-auth/js`, `react`, `nextjs`) — auth state was derived from the `localStorage` authz token alone, so after a reload (when the memory-only IdP token is gone) the app rendered as authenticated while every API request 401'd with `Missing IdP token`. `getUser()` / `isAuthenticated` now require a usable IdP token, so a reloaded session honestly reports as needing re-auth and the app shows login instead of a broken page.

### Added

- **`DuarAuthz.getAuthState()`** → `'authenticated' | 'needs_reauth' | 'unauthenticated'`, plus a `needsReauth` getter. `needs_reauth` = authz token present but IdP token gone (e.g. after reload).
- **`DuarAuthz.silentLogin(provider?)`** — seamless re-auth via a top-level `prompt=none` redirect to the IdP (full-page, not iframe; fresh nonce; `login_hint`; same-origin return-path; built-in loop guard). **`consumeReturnTo()`** returns the validated return path.
- **React `AuthzProvider` `autoReauth` prop** (opt-in) — automatically attempts `silentLogin()` on mount when `needs_reauth`. Context now exposes `authState`, `needsReauth`, `silentLogin`, `consumeReturnTo`. `AuthzCallback` gained an `onSilentReauthFailed` prop and passes `returnTo` to `onSuccess`.

### Breaking changes

- **`DuarAuthz.handleCallback()`** now returns a discriminated result — `{ status: 'success', idpToken, provider, returnTo } | { status: 'silent_failed', error, provider, returnTo } | null` — instead of `{ idpToken, provider } | null`. Migration: gate on `cb?.status === 'success'` before reading `cb.idpToken` / `cb.provider`. It also accepts an optional pre-captured hash argument for React StrictMode.

---

## [0.12.0] – 2026-06-15 — Organizations (email-domain tenancy)

Multi-tenant organizations keyed off verified email domains: users are resolved to
an organization at sign-in, workspaces can restrict membership to specific
organizations, and organization identity flows through JWT claims.

### Added

- **Organizations** — `organizations`, `organization_domains`, and `workspace_allowed_organizations` tables, plus `users.organization_id`. A seeded `public` organization is the default for users whose email domain matches no registered org.
- **Email-domain resolution** — sign-in normalizes the user's email domain and resolves it to an organization (`resolve_organization`), persisting `users.organization_id`.
- **Workspace org-restriction** — a workspace can be limited to one or more organizations; member invites and token issuance enforce the allow-list (`workspace_allows_org`).
- **Org admin API + UI** — CRUD for organizations, domains, and workspace allowed-orgs; admin pages for the organizations list/detail and a workspace **Access** tab.
- **JWT organization claims** — access and authz tokens carry `oid` / `oslug` / `opub` (organization id / slug / public flag), present-as-null when the user has no organization.

### Changed

- Sign-in (proxy mode) and `POST /authz/resolve` (authz mode) are organization-aware and enforce workspace allowed-orgs.
- The service version is now derived from the installed `duar-service` package metadata instead of a hardcoded literal — the admin System Health tab and OpenAPI now report the real version.
- Admin UI: **Client Apps → Login Apps** and **Service Apps → Services** (display labels only; the `client_id` login parameter, `service_name`, and the `X-Service-Key` header are unchanged).

### Fixed

- Admin System Health tab and OpenAPI metadata reported a stale `0.1.0`.
- Broken relative link in the proxy-mode tutorial that failed `mkdocs build --strict`.

### Breaking changes

- **Database migration required.** Run migrations (`make start` auto-migrates, or `alembic upgrade head`): adds the organizations tables and `users.organization_id`, and seeds the `public` organization.
- **Sign-in is organization-aware.** Users are resolved to an organization by email domain; workspaces configured with an allow-list reject members (and token issuance) for users outside their permitted organizations. Deployments that do not configure org restrictions default every user to the seeded `public` organization and are otherwise unaffected.

---

## [0.11.0] – 2026-05-29 — Security hardening

A co-ordinated fix for 17 findings across two rounds of deep security audit of
AuthZ mode (V1–V15 from round 1; V16–V18 from a follow-up round-2 review).
Core invariant reinforced: **clients cannot bypass IdP authentication**.

### Security

Each finding below is a fix. Downstream apps do not need to take action beyond
the migration steps in **Breaking changes** — no special remediation is
required on the caller side.

- **V1** — SDK middlewares (Python + Next.js) no longer skip IdP `aud` validation. Any Google/EntraID token from another OAuth client is now rejected.
- **V2** — Authz token's `svc` claim is now enforced by every consumer (server dependencies, Python middleware, Next.js middleware). Cross-service token replay is blocked.
- **V3** — `GET /authz/idp/github/login` validates `redirect_uri` against `ServiceApp.allowed_origins`. GitHub access tokens can no longer be exfiltrated to attacker-chosen sites.
- **V4** — `find_or_create_user` no longer auto-links accounts across IdPs by email. Cross-provider account takeover is blocked (see Breaking changes).
- **V6** — Authz tokens go through the same `jti` denylist + user-deactivation checks as access tokens. Captured authz tokens can now be revoked immediately, not only after their TTL.
- **V7** — `GET /auth/login/{provider}` now requires a `client_id` query parameter and binds it to the session. Authorization-code interception via redirect_uri substitution is blocked (see Breaking changes).
- **V8** — `DuarAuthz.handleCallback()` fails closed if no login flow is in progress in the current tab. Login-CSRF injection via crafted callback URLs is blocked.
- **V9** — `AuthzLocalStorageStore` no longer persists the IdP token to `localStorage`. XSS blast radius is limited to the short-lived authz token instead of the long-lived IdP token.
- **V10** — `require_admin` re-checks `users.is_active` and `users.is_admin` on every request. Flipping either flag takes effect on the next request instead of after the cookie TTL.
- **V11** — `ClientApp.redirect_uris` is validated via `urlparse` (scheme, host, no userinfo/fragment/query, round-trip). Rejects `https://good@evil.com/cb` and similar shapes.
- **V12** — `ServiceApp.allowed_origins` has the same strict validation. Rejects `"null"`, `"*"`, paths, query strings.
- **V13** — `email_verified` IdP claim check is now strict `is True` (rejects stringified `"false"`).
- **V14** — `POST /authz/resolve` accepts an optional `nonce` — when present, must match the IdP token's nonce claim. Enables replay protection for leaked IdP tokens.
- **V15** — Demo-authz backend CORS tightened (explicit methods + headers instead of `*`).
- **V5** — `POST /authz/resolve` no longer mints authz tokens for Origin-authenticated callers. Minting now requires an `X-Service-Key`. Origin-auth is still allowed for workspace discovery (no credential issued). Closes the "browser can mint authz tokens at will as long as the IdP token is valid" window. (See Breaking changes for migration.)
- **V16** — Refresh-family revocation now blacklists the paired access token's `jti`. `token_service.store_refresh_token`'s `access_jti` slot was always empty because `auth_service.issue_tokens` and `rotate_refresh_token` never forwarded it, leaving the access-token blacklist loop in `revoke_token_family` as dead code. On theft detection, the attacker's minted access token stayed valid for up to `access_token_expire_minutes` (default 15 min) after the family was killed. Fixed by decoding the minted access JWT and plumbing its `jti` into the refresh record.
- **V17** — `GET /authz/idp/github/callback` now validates the OAuth `state` parameter against the session value stored at login start (constant-time compare, rejected first). The login endpoint generated `state` but never stored it, and the callback did not accept a `state` query parameter — the GitHub-proxy AuthZ flow had no CSRF protection on the callback. Restores parity with proxy mode (which enforces state via Authlib).
- **V18** — Proxy-mode OAuth callbacks (`/auth/callback/{provider}` and `/auth/admin/callback/{provider}`) now use the same strict `is True` `email_verified` check as authz mode. V13 patched the helper in `idp_validator.py` but the two proxy-mode callbacks used an inline `not userinfo.get("email_verified", False)` that still accepted stringified booleans. Consolidated into a single `auth_service.is_email_verified_claim` helper used by all three paths.

#### Round 3 — key rotation + follow-up hardening (2026-05-29)

- **Graceful JWT signing-key rotation (closes ASVS MED-6)** — every token now carries a `kid`; JWKS publishes the current key plus any retired keys; `decode_token` selects the verifying key by `kid` (strict — unknown/missing `kid` rejected); the Python and JS/Next SDK middlewares resolve by `kid` and refetch JWKS on a rotated-in key. A leaked signing key can now be rotated without an outage. New config `JWT_PREVIOUS_PUBLIC_KEY_PATHS`; runbook at `docs/deployment/key-rotation.md`.
- **GitHub OAuth app-binding** — `idp_validator` verifies an opaque GitHub token was issued to Duar's own OAuth app (`POST /applications/{client_id}/token`) and fails closed when GitHub IdP is unconfigured. Blocks replay of a token minted for an attacker-registered OAuth app (the `aud`-equivalent for opaque tokens).
- **X-Forwarded-For rate-limit bypass** — the client IP is read from the configured trusted-proxy hop (`TRUSTED_PROXY_COUNT`, default 1) instead of the spoofable leftmost value, preventing per-IP throttle evasion behind a reverse proxy.
- **Pre-provisioned account linking** — refines V4: an admin-pre-provisioned account with no linked `SocialAccount` is now linked to the first matching IdP sign-in instead of being permanently blocked by `CrossProviderEmailConflict`. Genuine cross-provider collisions (account already has a SocialAccount under a different provider) are still rejected.
- **Hardening** — `register_resource` validates the owner is a workspace member (symmetric with share-grantee validation); workspace member search uses `icontains(autoescape=True)`; removed a misleading tautological workspace check in admin role assignment.
- **SDK** — Duar-token verification now uses PyJWT's `PyJWKClient` (consistent with the IdP-token path; handles `kid` selection + JWKS refetch), with bounded fetch timeouts. `AuthzMiddleware`'s offline design (no per-request revocation check at the SDK edge) is documented; deactivation enforcement there is bounded by the short authz-token TTL.

### Breaking changes

All breaking changes are server-side or SDK API shape. Caller code that follows
the `Before` pattern must be updated to match the `After` pattern. Each entry
also explains **Why** — useful for handling edge cases the simple patch doesn't
cover.

#### Python SDK — `duar_auth.Duar(mode="authz")` now requires `idp_audience`

**Before:**

```python
from duar_auth import Duar

duar = Duar(
    base_url="http://localhost:9003",
    service_name="my-service",
    service_key="sk_...",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
)
```

**After:**

```python
from duar_auth import Duar

duar = Duar(
    base_url="http://localhost:9003",
    service_name="my-service",
    service_key="sk_...",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience="123-abc.apps.googleusercontent.com",  # your OAuth client_id
    idp_issuer="https://accounts.google.com",           # recommended
)
```

**Why:** the middleware now enforces the IdP token's `aud` and (optionally) `iss` claims — without this, a signed ID token minted for *any* OAuth client of the same IdP would authenticate (including an attacker's app). The value must equal the OAuth client_id you registered with the IdP. `idp_issuer` defends against swapping the IdP entirely and is strongly recommended.

Env-var convention: `IDP_AUDIENCE` / `IDP_ISSUER` (or `GOOGLE_CLIENT_ID` for the google case).

#### Python SDK — `AuthzMiddleware` gains required `service_name` and `idp_audience`

Only affects direct users of `AuthzMiddleware` who do **not** go through `Duar.protect(app)` (which forwards these from the `Duar` instance automatically).

**Before:**

```python
app.add_middleware(
    AuthzMiddleware,
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    duar_public_key=duar_pem,
)
```

**After:**

```python
app.add_middleware(
    AuthzMiddleware,
    service_name="my-service",
    idp_audience="123-abc.apps.googleusercontent.com",
    idp_issuer="https://accounts.google.com",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    duar_public_key=duar_pem,
)
```

**Why:** the middleware now enforces the authz token's `svc` claim equals `service_name` (cross-service replay defence) and the IdP token's `aud` equals `idp_audience` (wrong-client defence). Construction will raise `ValueError` if either argument is missing.

#### JS SDK — `new DuarAuth(...)` (proxy mode) requires `clientId`

**Before:**

```typescript
import { DuarAuth } from '@duar-auth/js'

const auth = new DuarAuth({
  duarUrl: 'http://localhost:9003',
})
```

**After:**

```typescript
import { DuarAuth } from '@duar-auth/js'

const auth = new DuarAuth({
  duarUrl: 'http://localhost:9003',
  clientId: '00000000-0000-0000-0000-000000000000', // ClientApp UUID from admin panel
})
```

**Why:** `GET /auth/login/{provider}` now requires a `client_id` query param and validates `redirect_uri` against *that specific* ClientApp's `redirect_uris` (not any active app). Without the binding, an attacker could craft a login URL with another registered app's `redirect_uri` and intercept the auth code. The ClientApp UUID lives in the Duar admin panel under "Client Apps". Store it as an env var like `VITE_DUAR_CLIENT_ID` / `NEXT_PUBLIC_DUAR_CLIENT_ID`.

The `DuarAuth` constructor throws immediately if `clientId` is missing.

#### Next.js SDK — `createDuarAuthzMiddleware` requires `idpAudience` and `serviceName`

**Before:**

```typescript
// middleware.ts
export default createDuarAuthzMiddleware({
  duarUrl: process.env.DUAR_URL!,
  idpJwksUrl: 'https://www.googleapis.com/oauth2/v3/certs',
  publicPaths: ['/login', '/auth/callback'],
})
```

**After:**

```typescript
// middleware.ts
export default createDuarAuthzMiddleware({
  duarUrl: process.env.DUAR_URL!,
  idpJwksUrl: 'https://www.googleapis.com/oauth2/v3/certs',
  idpAudience: process.env.GOOGLE_CLIENT_ID!,
  idpIssuer: 'https://accounts.google.com',
  serviceName: 'my-app',
  publicPaths: ['/login', '/auth/callback'],
})
```

**Why:** same as the Python middleware — `idpAudience` is the defence against accepting tokens minted for other OAuth clients; `serviceName` is the defence against cross-service authz token replay. The factory throws at import time if either is missing.

#### Server — `GET /auth/login/{provider}` requires `client_id`

Only relevant if you call this endpoint directly (without the JS SDK, which now sets it automatically).

**Before:**

```
GET /auth/login/google?redirect_uri=https://app.example.com/callback&code_challenge=...&code_challenge_method=S256
```

**After:**

```
GET /auth/login/google?client_id=<ClientApp-UUID>&redirect_uri=https://app.example.com/callback&code_challenge=...&code_challenge_method=S256
```

**Why:** binds the flow to a specific ClientApp. `redirect_uri` is validated against that app's registered URIs only. Callback re-validates.

#### JS SDK — `DuarAuthz` requires `mintEndpoint`; minting routes through your backend

The browser no longer calls Duar's `/authz/resolve` directly to mint an authz token. It POSTs to a route on your own backend, which forwards to Duar with the service key. Discovery (listing workspaces) still goes to Duar directly — only the credential-issuance step is re-routed.

**Before (0.10.x and earlier):**

```typescript
import { DuarAuthz } from '@duar-auth/js'

const authz = new DuarAuthz({
  duarUrl: 'http://localhost:9003',
  idps: { google: IdpConfigs.google(GOOGLE_CLIENT_ID) },
})
// selectWorkspace() POSTed directly to Duar's /authz/resolve
```

**After (0.11.0):**

```typescript
import { DuarAuthz } from '@duar-auth/js'

const authz = new DuarAuthz({
  duarUrl: 'http://localhost:9003',
  mintEndpoint: '/api/auth/mint', // <— NEW: your backend route, NOT Duar
  idps: { google: IdpConfigs.google(GOOGLE_CLIENT_ID) },
})
// selectWorkspace() now POSTs to `/api/auth/mint` on your origin.
// The mint endpoint MUST be same-origin to the frontend (credentials: 'same-origin')
// or absolute on a CORS-allowed origin.
```

**You also need a backend route.** FastAPI example:

```python
# your-app/routes/auth.py
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from your_app.duar_instance import duar  # the Duar SDK instance

router = APIRouter()

class MintRequest(BaseModel):
    idp_token: str
    provider: str
    workspace_id: uuid.UUID
    nonce: str | None = None

@router.post("/api/auth/mint")
async def mint_authz_token(body: MintRequest):
    """Proxy: browser → here → Duar (with service key). Never expose the key."""
    try:
        return await duar.authz.resolve(
            idp_token=body.idp_token,
            provider=body.provider,
            workspace_id=body.workspace_id,
            nonce=body.nonce,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

**Add the route to `exclude_paths`** — it's hit before the user has a session:

```python
duar.protect(app, exclude_paths=[
    "/health", "/docs", "/openapi.json",
    "/api/auth/mint",  # <— NEW: login hasn't happened yet at this point
])
```

**Next.js Route Handler equivalent:**

```typescript
// app/api/auth/mint/route.ts
import { NextResponse } from 'next/server'

export async function POST(req: Request) {
  const body = await req.json()
  const r = await fetch(`${process.env.DUAR_URL}/authz/resolve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Service-Key': process.env.DUAR_SERVICE_KEY!, // server-side env only
    },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    return NextResponse.json(await r.json(), { status: r.status })
  }
  return NextResponse.json(await r.json())
}
```

**Why:** the old flow let any code running on a registered `allowed_origin` mint authz tokens as long as it had an IdP token. In practice, an XSS during a live session could keep re-minting for the IdP token's full TTL (~1h for Google). Routing the mint through your backend closes that window: XSS is limited to replaying the authz token that's already in the store (5-min TTL). Discovery stays browser-direct because it returns no credentials.

Cost: ~20 lines of backend proxy code per frontend; one new exclude path; one config field on `DuarAuthz`. No crypto, no token storage changes. Backward-compatible for any code that called `AuthzClient.resolve()` server-side with a service key (that's the service-key path the new mint endpoint itself uses).

#### Server — `find_or_create_user` raises `CrossProviderEmailConflict`

Callers are `/auth/callback/*` and `/authz/resolve`. The HTTP layer already handles this — the breaking change is for any code that imports and calls `find_or_create_user` directly.

**Before:**

```python
user = await auth_service.find_or_create_user(db, provider="github", provider_user_id="12345", email=email, name=name)
# If email matched an existing Google user, GitHub account was silently attached.
```

**After:**

```python
try:
    user = await auth_service.find_or_create_user(db, provider="github", provider_user_id="12345", email=email, name=name)
except auth_service.CrossProviderEmailConflict as e:
    # Email matched a user provisioned under a different IdP.
    # Ask the user to sign in with the original provider.
    return error_response(409, str(e))
```

**Why:** previous behaviour let an attacker who controlled a weaker IdP (e.g. a free personal EntraID account) impersonate a user originally provisioned from a stronger IdP (e.g. a corporate Google Workspace) as long as the emails matched. Identity is now keyed strictly on `(provider, provider_user_id)`.

Downstream impact: existing users are unaffected. Only *new* sign-ins where a different provider's email collides with an existing user are rejected.

HTTP surface:

- `POST /authz/resolve` returns `409 {"detail": "An account with email ... exists under a different identity provider..."}`
- `GET /auth/callback/{provider}` shows the "Email Already Used" HTML error page
- `GET /auth/admin/callback/{provider}` redirects with `?error=email_conflict`

### Changed

- **`AuthzLocalStorageStore` (JS SDK)** — the IdP token is now kept in instance memory only; it is no longer written to `localStorage`. The authz token and session metadata still persist. On page reload the SDK has no IdP token and treats the session as requiring re-authentication via the IdP login flow. Apps that relied on the old behaviour for silent refresh across reloads will observe a UX shift: users re-auth after reload. Apps needing true persistent sessions should front their frontend with a backend route that stores tokens server-side behind an `HttpOnly` cookie.

- **`ClientApp.redirect_uris` / `ServiceApp.allowed_origins` validation** — Pydantic now parses each entry with `urlparse` and rejects malformed shapes (no userinfo, no fragment, no query on URIs; no path/query/fragment on origins; no `"null"` / `"*"`). Existing values in the database are unaffected. `POST /admin/client-apps` / `PATCH /admin/service-apps/{id}` return `422` for bad inputs that previously passed.

- **`require_admin` (server)** — now re-reads the admin user's `is_active` + `is_admin` from the database on every admin request. Expect a small additional DB round-trip per admin API call. Demoting or deactivating an admin now takes effect on their very next request.

- **`AuthzMiddleware.__init__` (Python SDK)** — arguments are now keyword-only (`*`-prefixed). Positional calls break at import time.

### Added

- **Server** — `POST /authz/resolve` accepts an optional `nonce` field. When provided, the IdP token's `nonce` claim (OIDC only) must match. Browsers should pass the same nonce they generated at login start.
- **JS SDK** — `DuarAuthz.handleCallback()` throws `No login flow in progress — callback rejected` when `sessionStorage.duar_authz_nonce` is absent. Previously silently accepted.
- **Python SDK** — `auth_service.CrossProviderEmailConflict` exception type.
- **Admin UI** — `Login.tsx` renders new error codes `?error=email_conflict` and `?error=email_not_verified`.
- **Tests** — `test_authz_middleware.py` gains `test_wrong_audience_rejected` and `test_wrong_svc_rejected`.

### Fixed

- IdP `email_verified` check no longer accepts stringified `"false"` (a truthy non-empty string).
- Admin tokens now obey the user-deactivation denylist.
- GitHub proxy callback re-validates `redirect_uri` against the allowlist on return, not only at login start.

### Migration for downstream apps (quick checklist)

1. **Bump Duar SDK versions** everywhere Duar is used:
   - `pip install -U duar-auth` (Python)
   - `npm install @duar-auth/js@^0.11 @duar-auth/react@^0.11 @duar-auth/nextjs@^0.11` (JS)
2. **JS proxy-mode frontends** (`DuarAuth`): add `clientId` from env (source: Duar admin panel → Client Apps).
3. **JS authz-mode frontends** (`DuarAuthz`): add `mintEndpoint` (new backend route — see the breaking-changes entry above). Ship the backend route and add its path to `duar.protect(app, exclude_paths=[...])`.
4. **Python authz-mode backends**: add `idp_audience` (your OAuth client_id) and `idp_issuer` to the `Duar(...)` constructor.
5. **Next.js authz middlewares**: add `idpAudience`, `idpIssuer`, `serviceName` to `createDuarAuthzMiddleware(...)`.
6. **Any direct callers of `find_or_create_user`**: catch `CrossProviderEmailConflict`.
7. **If `AuthzLocalStorageStore` is used**: UX will require re-auth after page reload. If that's unacceptable, design a server-backed cookie store.
8. **Admin-panel-registered ClientApps / ServiceApps**: existing records unaffected. New admin panel submissions with trailing slashes, paths, or malformed shapes will now 422 — tighten input.

### Known deferred

_None at this release — V5 was originally deferred but is now included (see the `DuarAuthz` requires `mintEndpoint` breaking-change entry)._
