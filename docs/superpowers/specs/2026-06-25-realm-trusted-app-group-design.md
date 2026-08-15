# Realms — Trusted App Groups & M2M — Design

**Date:** 2026-06-25
**Status:** Approved (design)
**Branch:** `realm-trusted-app-group`
**Task:** Let an admin define a **realm** — a named group of service apps that fully
trust each other — so that (1) user sign-ins and permissions are shared across the
group, and (2) the apps can call each other's APIs both with a signed-in user and
with no user at all, all credentialed and validated through Duar.

## Problem

In authz mode, every service is hard-isolated by its unique `service_name`:

- **Permissions are siloed.** `verify_service_scope` (`service/src/api/dependencies.py:35`)
  enforces `caller.service_name == requested.service_name` on every permission/RBAC
  call, and `ResourcePermission` is unique on `(service_name, resource_type,
  resource_id)`. Two services cannot see each other's grants.
- **Authz tokens are single-service bound.** `/authz/resolve` mints a token whose
  `svc` claim is the calling service's name; `get_user_for_service_call`
  (`dependencies.py:243-244`, and again at `:313-314`) rejects it if `token.svc !=
  caller.service_name`. A token App A holds is *deliberately* unusable on App B.
- **No no-user m2m primitive.** Duar only issues credentials anchored to an
  IdP-authenticated user. There is no token an app can present to *another app* on a
  background/system call where no human is involved.

We want a set of apps that behave as **one logical trust boundary**: shared sign-in,
shared permissions, and trusted app↔app calls (with or without a user) — without the
apps containing any auth logic of their own. Everything routes through Duar.

## Model framing

A **Realm** groups `service_apps`. Its `slug` becomes a **shared scope** that all
members use in place of their individual `service_name` for permission scoping and
token audience. The realm is **orthogonal to workspaces** — `workspace_id` scoping is
unchanged; the realm only collapses the `service_name` dimension. A service belongs to
**at most one realm** (ambiguous scope otherwise).

Naming: we call it **Realm**, not "group", because the codebase already has a
user-facing **Group** (`grantee_type='group'` ACL grantee). "Realm" = shared
security/trust boundary, no collision.

Guardrail check: Duar's prime rule is *"proxy human identity from IdPs, never be
the auth authority for who a person is."* A no-user m2m token introduces **no human
identity** — it is a short-lived, transportable form of the **service identity**
Duar already issues and validates (`service_apps` keys). It carries service
identity only (never a synthesized user), so it is consistent with the guardrail's
intent even though it superficially resembles an OAuth `client_credentials` grant.

## Goals

- Admin-defined **realm** grouping service apps; one-realm-max per service.
- **Shared permissions**: all members read/write entity ACLs and RBAC actions under
  the realm's shared scope.
- **Shared sign-in**: a user's authz token works on every member app.
- **User-context m2m**: App A forwards the user's authz token to App B; B honors it.
- **No-user m2m**: App A mints a short-lived realm m2m token (with its service key) and
  presents it to App B; B trusts it as an in-realm system caller.
- **Anti-forgery rooted in Duar**, never app↔app trust: identity is
  server-stamped, tokens are RS256-signed, apps verify Duar's signature.
- **Hard network isolation**: the entire service-key surface lives on an unpublished
  internal listener the public internet cannot reach.
- Apps carry **no auth logic and no realm config** — the SDK self-discovers scope from
  Duar.
- **Non-breaking**: standalone services and existing data behave exactly as today.

## Non-Goals

- Federated/cross-readable permissions (rejected: chose single shared realm).
- Per-member least-privilege m2m in v1 (the `actions` field is *reserved* for it).
- Duar as a data-path reverse proxy between apps (apps call each other directly,
  carrying a Duar-issued credential).
- mTLS / service mesh between apps (overlay network + service keys suffice for now).
- Auto-migration of a joining service's pre-existing permission rows (documented
  fast-follow).
- Multi-realm membership; realm-owned workspaces.

## Decisions (locked in)

1. **One shared realm** — the group is one logical permission namespace + token
   audience. A grant/action is visible to every member; a realm token works on any
   member.
2. **`effective_scope`** = `realm.slug` for members, else the service's own
   `service_name`. This single resolved value substitutes for `service_name` in two
   surgical spots (scope check + svc-claim check).
3. **No-user m2m required** → a new `sentinel:m2m` token, service-key-minted,
   **server-stamped** identity, **full realm trust** in v1 with an `actions:["*"]`
   field that is enforceable later with **no migration**. Optional per-call
   `aud_target` reserved but **off by default**.
4. **Anti-forgery** = Duar RS256 signature + server-stamped `caller`/`svc`. Apps
   never trust each other directly.
5. **Hard network split** — two app instances (same image): a published **public**
   listener (humans) and an **unpublished internal** listener (all service-key
   endpoints).
6. **SDK self-discovers scope** via `GET /realm/whoami`; no app-side config.

## Data model

New table `realms` (`service/src/models/realm.py`):

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `slug` | Text unique | canonical shared scope, e.g. `acme-suite`; pattern `^[a-z][a-z0-9-]*[a-z0-9]$` |
| `name` | Text | display name |
| `m2m_ttl_s` | Int, default 300 | lifetime of no-user m2m tokens |
| `is_active` | Bool, default true | kill-switch; inactive realm cannot mint |
| `created_by` | UUID FK users SET NULL | |
| `created_at` / `updated_at` | timestamps | |

Membership — one nullable FK on the existing `service_apps` table
(`service/src/models/service_app.py:12`):

```
service_apps.realm_id  UUID NULL  FK -> realms.id  ON DELETE SET NULL
```

`NULL` = standalone (today's behavior, untouched). `client_apps` are not involved — in
authz mode the login-app identity is the service_app's `allowed_idp_audiences`
(`service_app.py:29`). Alembic: add `realms` + the nullable column; **no backfill**.

## The `effective_scope` mechanism

Add to `ServiceKeyContext` (`dependencies.py:21`) a resolved `realm_slug: str | None`
and a property:

```python
@property
def effective_scope(self) -> str:
    return self.realm_slug or self.service_name
```

Resolution happens once during key validation in `require_service_context`
(`dependencies.py:44`): extend `service_app_service.validate_key` to also return the
member's `realm_slug` (cache it in the existing `svc:key_cache` value, today
`"service_name:app_id"` → `"service_name:app_id:realm_slug"`).

Then the only two server-side comparisons change:

| Location | Today | After |
|---|---|---|
| `verify_service_scope` (`dependencies.py:35`) | `requested == ctx.service_name` | `requested == ctx.effective_scope` |
| svc-claim checks (`dependencies.py:243-244` **and** `:313-314`) | `token_svc == ctx.service_name` | `token_svc == ctx.effective_scope` |

Permission/RBAC rows are written/read under `effective_scope` — `ResourcePermission`,
`ServiceAction`, etc. all key on it. No permission-table schema change. The SDK, which
learns `effective_scope` from `whoami`, transparently substitutes it wherever it
sends `service_name` today, so app code is unchanged.

## Token flows

### Flow A — user-context (a human is behind the call)

```
User → App A (login)
App A backend → /authz/resolve (X-Service-Key + IdP token) → Duar
   mints authz token: svc = caller.effective_scope ("acme-suite")
App A → forwards authz token → App B
   App B SDK: type=authz, svc == my effective_scope?  ✓  → user identity + actions apply
```

Change: `/authz/resolve` (`service/src/api/authz_routes.py`) passes
`service_ctx.effective_scope` as the `service_name` arg to `create_authz_token`
(`service/src/auth/jwt.py:96`). Everything else (user `actions`, `wrole`) is already
realm-scoped because permissions key on `effective_scope`.

### Flow B — no-user (system/background call)

```
cron in App A (no human)
App A backend → POST /realm/m2m-token (X-Service-Key only) → Duar
   ← JWT { type=m2m, svc="acme-suite", caller="docs", actions=["*"] }
App A → forwards m2m token → App B
   App B SDK: type=m2m, aud=sentinel:m2m, svc == my effective_scope?  ✓
            → trusted in-realm system caller; actions=["*"] ⇒ full; logs caller
```

### Token shapes

**Authz token** (user-context) — one field changes:

```jsonc
{ "type":"authz", "sub":"<user>", "svc":"acme-suite", // ← was per-service name
  "actions":[...], "wid":"...", "wrole":"editor", ... }
```

**Realm m2m token** (new):

```jsonc
{ "iss":"<base_url>", "aud":"sentinel:m2m", "type":"m2m",
  "svc":"acme-suite",        // realm slug = effective_scope
  "caller":"docs",            // server-stamped: which member minted it (audit)
  "actions":["*"],            // full trust v1; enforce later, no migration
  "aud_target":null,          // reserved; when set, B checks == its service_name
  "jti":"...", "iat":..., "exp":"iat + realm.m2m_ttl_s" }
  // NO sub / email / user claims — an honest "no human" token
```

New audience constant `_AUD_M2M = "sentinel:m2m"` (`jwt.py`, alongside `:23-26`), kept
**separate** from access/authz/admin/refresh so a user-token validator can never
accept an m2m token as a user (token-type-confusion defense).

## Anti-forgery / trust model

Two independent, Duar-rooted checks — never app↔app trust:

**Mint-time** (App A → Duar): `require_service_key` gates the endpoint. The token's
`caller`/`svc` are **server-stamped from the authenticated key**, never client-asserted
— so a leaked key can only mint *that member's* token, not impersonate another member
or jump realms. Mint rejects if the service is not an active member of an active realm.

**Present-time** (App A → App B): App B verifies Duar's **RS256 signature** over
JWKS (existing `kid`-based resolution from the key-rotation work) — only Duar can
sign, so a fabricated token fails. Plus `aud==sentinel:m2m`, `svc==effective_scope`
(cross-realm replay dies), and short `exp`.

| Threat | Defense |
|---|---|
| Forged/fabricated token | RS256 signature — unforgeable without Duar's private key |
| Stolen service key | high-entropy, hashed, backend-only, rotatable; **server-stamped identity** blocks impersonation |
| Stolen token in transit | short TTL + TLS + `svc` binding; optional `jti` denylist for hard revoke |
| Malicious member | v1 = full trust by design; reserved `actions` field is the future least-privilege lever |

Network isolation (below) is **additive** — app-layer auth stays; the network just
removes the internet from the equation.

## Network posture — hard split

Two app instances built from shared routers (same image, two swarm services). A
`create_app(tier)` factory in `service/src/main.py` (refactor of `:185-245`) mounts
only one tier's routers:

```
TIER=public   uvicorn :9003  (published)    → admin, org-admin, auth (proxy mode),
                                               client-log, jwks*
TIER=internal uvicorn :9010  (NOT published, overlay-only)
                                            → realm, permissions, authz, roles(service)
```

- The internet has **no socket** to `:9010`; there is no proxy rule whose failure
  re-exposes internal routes — isolation is structural.
- Docker Swarm: both services on the `duar` overlay; `:9010` is **never published**;
  app services reach `http://duar-internal:9010` by name.
- Internal app drops CORS + OAuth session middleware (no browsers); keeps
  SecurityHeaders, rate limiting, RequestContext, AccessLog (`main.py:198-230`).
- `*`JWKS stays public by default (public keys are meant to be public); flip internal
  in an authz-only deployment where every verifier is a backend.
- **Router audit (implementation task):** `realm`, `permission_router`,
  `authz_router`, and the service-facing parts of `role_router` → internal (certain).
  `admin_router`, `org_admin_router`, `auth_router`, `client_log_router` → public
  (certain). `user_router`, `workspace_router`, `group_router` → audit each route's
  consumers; default **public** (they retain their own auth, so this never drops below
  today's exposure) and move internal only where service-key-only.

## API surface

Internal listener:

- `POST /realm/m2m-token` — `require_service_key`; body `{ target?: str }` (reserved,
  off by default); returns `{ token, expires_in }`. Rate-limited per service via the
  existing `service_or_ip_key` (`service/src/middleware/rate_limit.py:75`).
- `GET /realm/whoami` — `require_service_key`; returns `{ service_name,
  effective_scope, realm: { slug, name } | null }`. The SDK caches this.

New router `service/src/api/realm_routes.py`; new `create_m2m_token()` in `jwt.py`.

Public listener (admin auth + `X-Requested-With` CSRF):

- `/admin/realms` CRUD (`name`, `slug`, `m2m_ttl_s`) + membership add/remove, in
  `service/src/api/admin_routes.py`. Audit events `realm_created`, `realm_updated`,
  `realm_deleted`, `realm_member_added`, `realm_member_removed`. Delete guarded by
  type-to-confirm (existing convention); enforce one-realm-max on add.

New service `service/src/services/realm_service.py`; schemas
`service/src/schemas/realm.py`.

## SDK surface

| Package | Change |
|---|---|
| **Python** (`duar_auth`) | `whoami` scope discovery (cached); broaden authz `svc` check to `effective_scope`; `mint_m2m_token()` with auto-refresh at ~80% TTL; accept `type=m2m` → new `SystemAuth` context (no user; `caller`, `actions`) alongside `RequestAuth` |
| **JS** (`@duar-auth/js`, `react`, `nextjs`) | user-context `svc` check broadened to scope (via whoami); m2m **mint + accept** added to the **server** entry only (`@duar-auth/js` server, nextjs server helpers) — never browser |

m2m validation lives in the SDK (apps call each other directly). `["*"]` ⇒ full trust
now; the same code path enforces a narrowed `actions` list later with no change.

## Migration & rollout

- `realms` + nullable `service_apps.realm_id` → **non-breaking**, no backfill.
- A standalone service with **existing** permission rows that joins a realm will not
  see them under the new `effective_scope`. v1 = **no auto-migrate** (realms target new
  app-suites); the admin UI **warns** when adding a service that already has grants. A
  re-keying migration tool (`service_name` old → realm slug, with collision handling)
  is a documented fast-follow.

## Testing

- **Unit:** effective-scope resolution (member vs standalone); `verify_service_scope`
  and both svc-claim checks against `effective_scope`; mint stamps server-derived
  `caller`/`svc` (stolen key can't impersonate); m2m validation (sig/aud/svc/exp/type);
  cross-realm rejection; `aud_target` off-by-default; `whoami` payload.
- **Integration:** full user-context flow (A mints authz `svc=realm`, B accepts); full
  no-user flow (A mints m2m, B accepts); negatives (forged → sig fail, wrong-realm →
  reject, expired → reject, non-member → cannot mint usefully).
- **Network:** a test asserting internal routes are not mounted on the public app and
  vice-versa.
- Follow existing patterns (`service/tests/test_rate_limit_*`, `test_ratelimit_event`).
  New: `test_realm_scope`, `test_realm_m2m`, `test_route_tiers`.

## Build sequence

1. **Data + scope core** — `realms` model/migration, `realm_id` FK, `effective_scope`
   on `ServiceKeyContext`, the two comparison changes, `validate_key` cache change.
   (Permissions become realm-shareable; standalone unaffected.)
2. **User-context m2m** — `/authz/resolve` stamps `effective_scope`; `whoami`. (Flow A
   works end-to-end.)
3. **No-user m2m** — `_AUD_M2M`, `create_m2m_token`, `POST /realm/m2m-token`, SDK
   `SystemAuth` accept/mint. (Flow B works end-to-end.)
4. **Network split** — `create_app(tier)` factory, router audit, swarm compose two
   services, `:9010` unpublished.
5. **Admin** — `/admin/realms` CRUD + membership, React Realms page, Service App detail
   shows realm, join-with-existing-grants warning.
6. **Docs + tests** — guide/api/sdk docs, the test suites above.

## Future work

- Per-member least-privilege m2m (enforce a narrowed `actions` list).
- Per-call `aud_target` narrowing (reduce a leaked token's blast radius to one target).
- Re-keying migration tool for services joining a realm with existing grants.
- Optional `jti` denylist for hard m2m-token revocation.
- mTLS between apps if the team/topology outgrows overlay + service keys.
