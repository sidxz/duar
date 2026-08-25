# Self-serve workspaces (hosted onboarding + invitations) — Design

> Approved 2026-08-25; amended the same day after a three-lens fresh-eye
> review (security, codebase fit, simplicity) — see the last section.
> Public-facing app deployments (DocuStore's public demo) need users who sign
> in with Google/GitHub and have **no** workspace to get one without a Duar
> admin in the loop: either **join** a workspace they were invited to (the
> encouraged path) or **create** one and own it. Today AuthZ mode has zero
> workspace-creation paths and "invite" means a silent add of an
> already-existing user. This change adds Duar-hosted onboarding pages and a
> consent-based invitation model, **flag-gated, default off**, so the
> consortium instance is byte-identical unless the flag is set. Client apps
> change by two links; SDKs are untouched.

## Decisions (from brainstorm + review)

- **Hosted on Duar, not in the SDKs.** Duar serves server-rendered HTML pages
  under `/onboard`. Apps link to them ("Sign up" / "Invite people"). No SDK or
  proxy allowlist changes.
- **One flag, one meaning.** `SELF_SERVE_ENABLED=false`. Off: `/onboard*` is
  404, proxy-mode `POST /workspaces` is 403 (today it is open to any user who
  already holds a workspace-scoped access token — a latent gap on the
  consortium instance, now closed), no invitation can be created or redeemed.
  On: everything below.
- **Join first, create second.** The onboarding page leads with the invite
  link's workspace and "have a code?"; creation is a deliberate secondary
  action. No JIT "Personal" workspace on sign-in — every drive-by login would
  mint a workspace, the user never chooses, and an invitee who signs in before
  redeeming would end up with two.
- **Consent is mandatory in self-serve mode.** Nobody can put a user into a
  workspace without that user's own POST. The legacy direct-add
  (`POST /workspaces/{id}/members/invite`, an email-existence oracle) returns
  403 while the flag is on. Duar-admin paths (admin panel add-user, CSV import)
  are unchanged on every instance — the Duar admin is trusted everywhere.
- **One invitation kind: a one-time link**, optionally locked to an email.
  (Review: email-bound "discovery" invites were cut — Duar sends no email, so
  the inviter has to message the invitee either way, and a link can carry the
  way back to the app; the non-forwardable property survives as an optional
  email lock on the code.) Single-use, 7-day TTL, never `owner`, hashed at
  rest, and **valid only while the inviter is still an owner/admin** of the
  workspace.
- **Anti-abuse is layered and cheap**: IdP-verified identity is the CAPTCHA
  (Google-only recommended on public instances), a per-user creation cap
  (`0` = join-only mode, the soft brake), and an instance-wide creation
  breaker that counts **successful** creations in the last hour. No signup
  code, no marker column (cut as speculative; both are easy to add later).
- **Identity for hosted pages = Duar's own OAuth client flow** (authlib code
  flow with `state`, same as proxy mode and admin login), ending in the
  existing signed session cookie. No new JWT audience, no IdP-token replay
  surface (the hosted flow never accepts a raw IdP token).
- **No JavaScript on hosted pages.** Jinja2 with autoescape, `default-src
  'none'`, plain forms. Smallest possible XSS surface for a page that
  strangers reach.

## Threat model

The trust shift: in self-serve mode a *workspace admin* is an untrusted
stranger, not a consortium administrator. Everything a workspace admin can do
to **other** people must require those people's action. Invariants:

| Invariant | Enforced by |
|---|---|
| Flag off ⇒ no new behavior | `/onboard*` 404, `POST /workspaces` 403, `invitation_service.{create,redeem,revoke}` raise `SelfServeDisabled` (existing rows stay inert, nothing is deleted) |
| No consent-free membership | membership is created only on the invitee's authenticated POST; direct-add 403 under the flag |
| An invitation dies with its inviter's standing | the single-use claim `UPDATE` also requires `created_by` to currently hold `owner`/`admin` in that workspace; a removed or deleted inviter's pre-minted links are dead |
| An invitation never changes an existing member's role | already-member redeem is a no-op on the membership (consumes the invitation, role untouched) |
| Email lock redeemable only by that email | `invitation.email` (lowercased) must equal `lower(user.email)` (IdP-verified); mismatch ⇒ the same generic "invalid or expired" |
| Bearer code unguessable, unreplayable | 32-byte `secrets.token_urlsafe`, SHA-256 at rest, atomic single-use claim |
| Invitation cannot escalate | `role ∈ {admin, editor, viewer}` (check constraint); creator must be `owner`/`admin` of that workspace at create **and** at redeem |
| Org allowlist holds | `assert_user_allowed_in_workspace` on every redeem (same gate as `invite_member`/`issue_tokens`), before the claim, so a rejected redeem does not consume the invitation |
| No open redirect | `return_to` (≤ 2048 chars) must pass `_validate_authz_redirect_uri` (origin ∈ active `ServiceApp.allowed_origins`); otherwise 400 and nothing stored; applies to `GET /onboard` and `GET /onboard/invites` alike, and again when a link that embeds `return_to` is opened |
| Hosted session ≠ admin session | `/onboard` reads only the session cookie; `require_admin` reads only `admin_token`; neither grants the other |
| Deactivated user is cut off | every `/onboard` request that loads `onboard_user_id` re-reads the user; missing or `is_active=False` ⇒ session cleared, 302 `/onboard` |
| CSRF | session cookie is `SameSite=Lax` (no cross-site POST) **and** every form carries a session-bound token compared with `compare_digest` (checked after the session check, so an expired session 302s instead of 403ing) |
| Clickjacking | `X-Frame-Options: DENY` + `frame-ancestors 'none'` already on every response |
| Forms can only post to Duar | `_HTML_CSP` changes `form-action 'none'` → `'self'` (error pages have no forms; nothing else in the policy moves); "Continue to app" stays a link, never a POST redirect |
| Nothing hosted is cacheable | `/onboard` joins the `Cache-Control: no-store` prefix list |
| Codes stay out of history | `GET /onboard?code=…` stores and answers `303 /onboard` (PRG) so the query never persists as a history entry; Duar's access log records only the route template |
| Bounded creation | per-user cap and pending-invitation cap under a `FOR UPDATE` row lock (user row / workspace row) so concurrent requests cannot exceed them; breaker counts successes, so unauthenticated requests cannot exhaust it |
| Audit | every create/accept/revoke writes an activity row and a `log_security` event; the plaintext code appears in neither |

Residual risks, accepted:

- **IdP email trust.** The email lock (and the pre-existing bare-account
  linking in `find_or_create_user`) is as strong as the IdP's email
  verification. Google/GitHub: strict. Entra: the tenant pin does **not**
  make `email` verified (guest accounts carry an inviter-supplied address),
  so `/onboard/callback/entra_id` additionally requires `xms_edov is True`
  (the optional "email domain owner verified" claim must be enabled on the
  app registration) — documented, and Entra is not recommended on a public
  instance at all.
- **One human, many IdP accounts** bypasses the per-user cap. Bounded by the
  breaker and the Google-only recommendation (phone-verified accounts).
- **Create → delete → create** loops (owner deletes in proxy mode) are bounded
  only by the breaker; the cap counts live rows.
- **Session cookie theft** yields ≤ 10 minutes of onboarding actions as that
  user; `HttpOnly`, `Secure` in prod. Logout is client-side (`session=null`);
  the old value stays verifiable until its timestamp expires — within the
  accepted window. Hardening the cookie name to `__Host-session` when
  `COOKIE_SECURE=true` is worthwhile but touches every OAuth flow on every
  instance; deferred to its own change.
- **Edge logs** (ingress in front of Duar) contain `?code=` URLs; codes are
  single-use and expire in 7 days.
- **`/authz/resolve` rate bucket is per calling service** (`60/min`). Not
  changed here; public deployments raise `RATE_LIMIT_AUTHZ_RESOLVE` and rely
  on edge per-IP limiting, the documented posture for volumetric protection.

## 1. Configuration

```
SELF_SERVE_ENABLED=false                 # master switch
SELF_SERVE_MAX_WORKSPACES_PER_USER=1     # count of workspaces where created_by = user; 0 = join-only mode
SELF_SERVE_MAX_CREATES_PER_HOUR=30       # instance-wide: workspaces created in the trailing hour (successes only)
```

- Startup: flag on and `get_configured_providers()` empty ⇒ log a warning
  (`config.self_serve.no_providers`) — onboarding would be a dead end.
- `/admin/system/settings` gains `self_serve: {enabled, max_workspaces_per_user,
  max_creates_per_hour}` (JSON only; no admin UI change in v1).
- Invitation TTL (7 days) and pending cap (50/workspace) are constants.
- No new rate-limit tier: the breaker is a DB count, not a slowapi limit, so
  `_validate_decorated_tier`, `rate_limit_report()` and
  `test_rate_limit_config.py` are untouched.

## 2. Data model (one Alembic migration, `down_revision = "e4b7a2c9d1f3"`)

```
workspace_invitations
  id            uuid pk
  workspace_id  uuid fk workspaces(id) on delete cascade, not null
  code_hash     text not null unique      -- sha256 hex of the bearer code
  email         text null                 -- optional lock, lowercased/stripped
  role          text not null  check (role in ('admin','editor','viewer'))   name ck_invitation_role
  created_by    uuid fk users(id) on delete set null
  created_at    timestamptz not null default now()
  expires_at    timestamptz not null
  accepted_by   uuid fk users(id) on delete set null, null
  accepted_at   timestamptz null
  revoked_at    timestamptz null
  index ix_invitations_workspace (workspace_id)
```

"Pending" ≡ `accepted_at IS NULL AND revoked_at IS NULL AND expires_at > :now`
with `:now` a Python-side `datetime.now(UTC)` parameter (the SQLite test
sessions have no `now()`). Check constraints live in the model's
`__table_args__` (as `WorkspaceMembership` does) so `create_all` applies them
in tests; the migration mirrors them with the `ck_*` naming convention.

## 3. Service layer

`service/src/services/invitation_service.py`

- `create(db, *, workspace_id, role, created_by, actor_role, email=None) -> (Invitation, code)`.
  Raises `SelfServeDisabled` when the flag is off; refuses `actor_role ∉
  {owner, admin}`, `role == "owner"`, a malformed `email` (strip, lowercase,
  exactly one `@`, ≤ 254 chars), or a 50th pending invitation — the pending
  count runs after `SELECT … FROM workspaces WHERE id = :wid FOR UPDATE` in
  the same transaction. Generates `secrets.token_urlsafe(32)`, stores its
  SHA-256, returns the plaintext once.
- `list_for_workspace(db, workspace_id, now)` — pending only; never returns
  the hash.
- `revoke(db, invitation_id, *, actor_id, actor_role)` — owner/admin of that
  workspace; sets `revoked_at`.
- `redeem(db, user, code, now) -> Workspace`. Order: flag → lookup by
  `sha256(code)` → email lock (if set) → `assert_user_allowed_in_workspace`
  (its own error, safe to disclose; invitation **not** consumed) → atomic
  claim:
  ```sql
  UPDATE workspace_invitations i SET accepted_by = :uid, accepted_at = :now
  WHERE i.id = :id AND i.accepted_at IS NULL AND i.revoked_at IS NULL
    AND i.expires_at > :now
    AND EXISTS (SELECT 1 FROM workspace_memberships m
                WHERE m.workspace_id = i.workspace_id
                  AND m.user_id = i.created_by
                  AND m.role IN ('owner','admin'))
  ```
  rowcount ≠ 1 ⇒ `InvitationInvalid` (one generic error for unknown / used /
  expired / revoked / wrong email / inviter no longer admin). Then insert the
  membership; if one already exists it is left exactly as is (role never
  raised or lowered) and the call still succeeds.
- `peek(db, code, now) -> Invitation | None` — read-only lookup for the
  `/home` card (same predicates as the claim, no write).

`service/src/services/workspace_service.py`

- `create_self_serve(db, user, name, slug=None, now) -> Workspace`: flag →
  `SELECT … FROM users WHERE id = :uid FOR UPDATE` → per-user cap
  (`count(workspaces where created_by = :uid)` ≥ cap ⇒ `SelfServeCapReached`;
  cap `0` always raises) → breaker (`count(workspaces where created_at > :now
  - 1h)` ≥ `max_creates_per_hour` ⇒ `SelfServeThrottled`) → slug = caller's if
  given (proxy-mode API) else `slugify(name)[:40] + "-" + secrets.token_hex(2)`
  where `slugify` = lowercase, every run of non-`[a-z0-9]` → `-`, trimmed of
  `-`, `"ws"` if empty (retry on collision ≤ 3; a caller-supplied collision is
  a 409 as today) → `create_workspace(...)`. Creator becomes owner as today.
  `FOR UPDATE` compiles to nothing on SQLite, which is fine for the tests.

Naming — two schemes, deliberately: **activity rows** use the repo's
snake_case actions and are what the admin SPA filters on
(`admin/src/pages/Activity.tsx` list + `charts.tsx` prefix regexes, both
extended): `workspace_created` (detail `self_serve: true`),
`self_serve_denied` (detail `reason: cap|throttled|disabled`),
`invitation_created|accepted|revoked|rejected` with `target_type="workspace"`
(the `_TARGET_MODELS` map has no invitation type). **`log_security` events**
use the dotted stream names: `workspace.self_serve.created|denied`,
`invitation.created|accepted|revoked|rejected` (`reason=invalid|org_not_allowed`).
The plaintext code and the hash appear in neither (extend
`test_no_raw_pii_logging.py`).

## 4. Hosted routes — `service/src/api/onboard_routes.py`, prefix `/onboard`

Registered in `PUBLIC_ROUTERS` (the session middleware is public-tier only;
`test_app_tiers.py` gains `/onboard`). All responses are HTML with
`X-CSP-Override: html-page`; `_error_page` is imported from `auth_routes`
(not circular — it imports nothing from `src.api` but `dependencies`) and
gains an optional `back_href` so every hosted error page offers "Back to
sign-in" (`{base_url}/onboard`). Every route returns 404 when the flag is off.

Session keys: `onboard_user_id`, `onboard_csrf`, `onboard_return_to`,
`onboard_code`, `onboard_next`, `onboard_flash`, `onboard_result`,
`onboard_seen`. Rules: `GET /onboard` deletes only `onboard_code`,
`onboard_return_to`, `onboard_next`, `onboard_flash`, `onboard_result` —
never `onboard_user_id`/`onboard_csrf`, and never `session.clear()` (that
would kill an in-flight proxy/admin/authz-idp round-trip in another tab).
Every `/onboard` GET that has a session user rewrites `onboard_seen` so the
10-minute cookie window slides (Starlette re-issues the cookie only on write).
A `/onboard` POST checks the session **before** the CSRF token: no session ⇒
302 `/onboard` (never 403). All hrefs, form actions and redirect `Location`s
are absolute from `settings.base_url` (path-prefix deployments; ingress strips
the prefix).

| Route | Behavior |
|---|---|
| `GET /` | Query: `return_to?` (validated, see threat model), `code?`, `provider?`. Resets the keys listed above, stores the validated `return_to` and `code`, then **303 `/onboard`** (PRG). On the bare GET: signed in ⇒ 302 `/onboard/home` (or `onboard_next`); else if exactly one provider is configured, or `?provider=` names a configured one ⇒ 302 `/onboard/login/{p}`; else provider buttons. |
| `GET /login/{provider}` | `@limiter.limit(rate_limit_auth)` per IP (needs `request: Request`). `authorize_redirect` to `{base_url}/onboard/callback/{provider}`. Unconfigured ⇒ 400 error page. |
| `GET /callback/{provider}` | `rate_limit_auth`. authlib `authorize_access_token`; profile via `_idp_profile(client, token, provider)` **factored out of the proxy and admin callbacks** — it returns `(provider_user_id, email, name, avatar_url, provider_data)` and raises `IdpProfileError(reason, count_for_stuffing)` (`email_not_verified` / `no_email`); each caller logs with its own `flow` and renders its own error. For `entra_id` the onboarding caller additionally requires `xms_edov is True`. Then, mirroring the proxy callback: `resolve_organization` (None ⇒ 403 page, `_log_login_failure(..., "org_not_permitted", flow="onboard")`) → `find_or_create_user` (commits internally; `CrossProviderEmailConflict` ⇒ 409 page with the existing message) → `is_active` (False ⇒ 403) → activity `user_login` (detail `flow: "onboard"`), commit, `signal_service.on_login_success(...)`, `log_security("auth.login.succeeded", flow="onboard")`. Never `session.clear()`. Sets `onboard_user_id` + fresh `onboard_csrf`; 302 to `onboard_next` if set else `/onboard/home`. |
| `GET /home` | Requires session user. Sections, empty ones hidden: (1) the stored code — `peek`; valid ⇒ "Join **{ws}** as {role}" card with Join (POSTs the code); already a member ⇒ "You're already a member of {ws}" with Continue and no Join; invalid ⇒ inline notice and the key is cleared. (2) "Have an invite link or code?" — accepts a bare code or a full `/onboard?code=` URL (the `code` param is extracted). (3) Workspaces the user is already in, with Continue. (4) "Create a workspace" — name field; replaced by "limit reached" text when at cap (cap `0` ⇒ section absent). Footer: "Invite people" (→ `/invites`), "Back to app" when `return_to` is set, Sign out. |
| `POST /join` | `rate_limit_auth`. Form: `csrf`, `code`. `redeem`; success ⇒ `onboard_result` and 302 `/onboard/done`; `InvitationInvalid` ⇒ flash "This invitation is invalid, expired, or already used."; org error ⇒ flash its message. |
| `POST /create` | `rate_limit_auth` per IP only. Form: `csrf`, `name` (`strip_html`, 1–255 — `SafeStr` does not apply to `Form()` params). `create_self_serve`; `SelfServeCapReached` ⇒ flash; `SelfServeThrottled` ⇒ 429 HTML error page ("Too many workspaces are being created right now — try again later") and `self_serve_denied reason=throttled`. Success ⇒ 302 `/onboard/done`. |
| `GET /invites?workspace=&return_to=` | Validates/stores `return_to` like `GET /`. No session ⇒ store `onboard_next = "/onboard/invites?workspace=…"` and 302 `/onboard`. Lists workspaces where the user's role ∈ {owner, admin}; preselects `?workspace` if the user qualifies there. For the selected workspace: pending invitations (email lock or "anyone", role, expires, Revoke) and the create form: role (viewer default / editor / admin), email lock (optional). "Back to app" when `return_to` is set. |
| `POST /invites` | `rate_limit_auth`. Form: `csrf`, `workspace_id`, `role`, `email?`. The link `{base_url}/onboard?code=…[&return_to=…]` (the inviter's validated `return_to`, if any) is rendered **once** in a wide read-only `<input>` with the bare code beneath and "Shown once — copy it now"; afterwards the pending list shows only `anyone|<email> · role · expires`. |
| `POST /invites/{id}/revoke` | `csrf`; owner/admin of that invitation's workspace. |
| `GET /done` | Renders `onboard_result` ("You joined X" / "You created X"); "Continue to app" link when `onboard_return_to` is set, otherwise "You're all set — go back to the app and sign in". Clears `onboard_code`, `onboard_result`; keeps the session so the user can go invite people. |
| `POST /logout` | Clears the session. Link on every signed-in page. |

Templates: `service/src/templates/onboard/{base,login,home,invites,done}.html`,
`Jinja2Templates(..., autoescape=True)`; `src/templates/` ships via the
Dockerfile's `COPY src/ src/`. **`jinja2` becomes an explicit `service`
dependency** — the image runs `uv sync` from `service/pyproject.toml` alone;
today jinja2 is in `uv.lock` only as a docs-tooling transitive. Visuals: the
`_error_page` card (dark, red header, system font); no external assets;
labelled form fields.

Middleware changes (`security_headers.py`): `_HTML_CSP` `form-action 'self'`
(update the pinned test `test_security_headers_csp.py`); `/onboard` added to
the `no-store` prefix list (new test).

### Legacy route changes

- `POST /workspaces` (proxy mode, `workspace_routes.py:41`): `403 {"detail": "Workspace creation is disabled on this server"}` unless the flag is on. When on, it calls `create_self_serve(slug=body.slug)` — same cap and breaker (cap ⇒ 403, throttled ⇒ 429 JSON via the normal handler). Stays undecorated (per-IP defaults apply). `test_workspace_audit_events.py` monkeypatches the flag on and gains a flag-off 403 case; `docs/api/resources.md`, `docs/security.md`, `docs/guide/workspaces.md` are updated.
- `POST /workspaces/{id}/members/invite`: 403 `{"detail": "Direct member add is disabled in self-serve mode; use invitations"}` when the flag is on.

## 5. App integration (what DocuStore does)

- Put "Sign up / Join a workspace" → `{DUAR_URL}/onboard?return_to={app login page}` permanently on the login page, and have `errorComponent` say "No workspace yet — sign up" for the zero-workspace error. (The React SDK's default text is unchanged; matching on it is not required.)
- "Invite people" (owners/admins) → `{DUAR_URL}/onboard/invites?workspace={wid}&return_to={app login page}`.
- `return_to` must be a page that **starts sign-in**: after onboarding the app has no session, and in AuthZ mode `handleCallback` has already cleared `duar_authz_provider` before reporting zero workspaces (`authz-client.ts:181`), so `silentLogin()` without a provider argument is a no-op — pass the provider explicitly or budget one "Sign in" click. The IdP session is live, so the sign-in itself is silent, and `/authz/resolve` (or `/auth/workspaces`) now lists the workspace.

## 6. Deployment checklist (public instance)

1. `SELF_SERVE_ENABLED=true`; review the two limits (`MAX_WORKSPACES_PER_USER=0` = join-only).
2. **An enabled public (catch-all) organization must exist** — otherwise unclaimed domains get "Sign-in not permitted" at the callback and self-serve is dead on arrival.
3. Configure Duar's **own** IdP client for the code flow: `GOOGLE_CLIENT_ID` (often already set for the AuthZ audience) **and** `GOOGLE_CLIENT_SECRET`; register `{BASE_URL}/onboard/callback/{provider}` on the **same** Google OAuth client the app uses (one "Web application" client holds both the SPA's implicit redirect and Duar's code-flow redirect).
4. Prefer Google only. GitHub accounts are cheap for bots. Entra: not recommended; if used it must be single-tenant **and** expose `xms_edov`.
5. The app's origin is already on `ServiceApp.allowed_origins` for AuthZ CORS — that is the `return_to` allowlist; nothing extra to register.
6. `TIER` must include the public listener (`public` or `all`); `/onboard` is a public-tier router.
7. Raise `RATE_LIMIT_AUTHZ_RESOLVE` (per-service bucket) and put per-IP edge rate limiting in front of Duar; edge logs will contain single-use `?code=` URLs.
8. `COOKIE_SECURE=true`, `SESSION_SECRET_KEY` set (already required in prod).
9. Consortium safety: with the flag unset the only observable change of this release is `POST /workspaces` → 403.

## 7. Testing

Service suite (`TestClient` + dependency overrides for gates; SQLite-backed
sessions for the service layer as in `test_actions_insights.py`, adding the
new table and the users/workspaces/memberships/org tables to its `_TABLES`
list):

- Flag off: every `/onboard*` route 404; `POST /workspaces` 403; `create`/`redeem`/`revoke` raise `SelfServeDisabled` even with a valid pending row.
- Flag on: direct-add `members/invite` 403; admin add-user and CSV import unchanged.
- `redeem`: wrong email lock ⇒ generic invalid; expired / revoked / already used ⇒ generic invalid; **inviter demoted to editor or removed ⇒ generic invalid**; second sequential redeem fails (true concurrency is Postgres-only — the atomicity is the single conditional `UPDATE`); org-not-allowed ⇒ org error and invitation **not** consumed; already-member ⇒ success, invitation consumed, role unchanged.
- `create`: `role=owner` rejected; non-admin actor rejected; 51st pending rejected; email lock lowercased/stripped; code returned once and only its hash stored; hash and code absent from activity detail and log fields.
- `create_self_serve`: cap by `created_by`; cap `0` always refuses; breaker refuses at `max_creates_per_hour`; slug collision retry; creator is owner; flag-on `POST /workspaces` goes through the same function.
- Hosted: `return_to` off-allowlist or > 2048 ⇒ 400 and not stored; on-allowlist ⇒ rendered on `/done` and embedded in generated links; `GET /onboard?code=` ⇒ 303 with the code in the session; POST without session ⇒ 302; POST with wrong csrf ⇒ 403; `GET` never mutates; deactivated user ⇒ session cleared; `onboard_next` honored after callback; `entra_id` without `xms_edov` ⇒ 403.
- Headers: `/onboard/home` carries `form-action 'self'` and `Cache-Control: no-store`; the JSON API CSP is unchanged.
- Session isolation: an `admin_token` cookie alone does not authenticate `/onboard/home`; an onboarding session does not authenticate `/admin/*`.
- Callback parity: `_idp_profile` refactor lands as its own commit with `test_email_verified_strict.py` / `test_login_failure_audit.py` green; onboarding callback writes `user_login` and calls `on_login_success`.
- One manual browser click-through on localhost (Google SSO, one account plus an incognito window): create, invite by link, accept, invite with email lock, revoke, continue-to-app.

## 8. Docs and changelog

- `docs/guide/self-serve.md`: concept, both flows as step lists, the app
  integration snippet, the deployment checklist, the threat model summary.
  Nav entry in `mkdocs.yml`.
- Config rows in `docs/deployment/environment.md` and
  `docs/getting-started/configuration.md`; the `/onboard/callback/*` redirect
  URIs next to the existing `/auth/callback/*` table; `docs/security.md`
  paragraph on the flag and the consortium tightening; `docs/guide/workspaces.md`
  and `docs/api/resources.md` for the `POST /workspaces` / direct-add changes.
- `CHANGELOG.md` 1.3.0 (Unreleased): feature entry + **behavior change**:
  proxy-mode `POST /workspaces` now requires `SELF_SERVE_ENABLED`.

## 9. Non-goals (v1)

SDK changes (a later 1-liner may add `onboard_url` to resolve discovery);
JSON invitation API for apps that want native UI; admin-panel invitation
views or a self-serve filter; email-bound *discovery* invites (an invitee
seeing invitations without a link); signup code; a `self_serve` marker
column; `__Host-` session cookie prefix (global hardening, own change);
invitee "decline"; "leave workspace"; approval queue; dormant-workspace
cleanup; multi-use codes; a token claim for self-serve tier; Duar sending
email; proxy-mode `ClientApp.redirect_uris` as a `return_to` source (AuthZ
apps are ServiceApps; a proxy-mode public app registers its origin as a
ServiceApp `allowed_origins` entry or omits `return_to`).

## Review amendments (2026-08-25, fresh-eye review: security / codebase-fit / simplicity)

What changed from the approved draft and why:

- **Blocker fixed:** the `html-page` CSP override carries `form-action 'none'`
  (`security_headers.py:128-131`, pinned by a test) — every hosted form would
  have been dead on arrival. Now `form-action 'self'`.
- **New invariant:** an invitation is valid only while its inviter is still
  owner/admin (a removed admin could otherwise re-enter through a pre-minted
  link); enforced inside the atomic claim.
- **Breaker redesigned:** a slowapi decorator counts *requests* before auth,
  so 30 anonymous POSTs would have disabled creation for everyone for an
  hour; it is now a DB count of successful creations. This also removed a
  rate-limit tier and four config/admin touch points.
- **Simplified:** one invitation kind (link + optional email lock) instead of
  email-bound-xor-code; signup code and `self_serve` column cut; admin UI row
  cut (JSON mirror kept).
- **Correctness details:** `FOR UPDATE` row locks on both caps; PRG on
  `GET /onboard?code=`; `/onboard` in the `no-store` list; `is_active`
  re-check per request; sliding session via a write per GET; session-key
  rules (no `clear()`); callback parity (`user_login` + `on_login_success`);
  Entra `xms_edov` on the hosted callback; `return_to` on `/invites` and in
  generated links; `onboard_next` deep link; single-provider auto-redirect
  and `?provider=` (fixes the cross-provider 409 dead end); `strip_html` on
  form input; activity vs. `log_security` naming and the admin SPA
  allowlists; `jinja2` explicit dependency; migration `down_revision`;
  SQLite-safe `:now` parameter.
- **Deployment checklist:** enabled public org required; Google client
  secret + callback registration on the same OAuth client; `TIER`;
  consortium safety line.
