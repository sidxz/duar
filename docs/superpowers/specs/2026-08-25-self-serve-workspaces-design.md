# Self-serve workspaces (hosted onboarding + invitations) — Design

> Approved 2026-08-25. Public-facing app deployments (DocuStore's public demo)
> need users who sign in with Google/GitHub and have **no** workspace to get
> one without a Duar admin in the loop: either **join** a workspace they were
> invited to (the encouraged path) or **create** one and own it. Today AuthZ
> mode has zero workspace-creation paths and "invite" means a silent add of an
> already-existing user. This change adds Duar-hosted onboarding pages and a
> consent-based invitation model, **flag-gated, default off**, so the
> consortium instance is byte-identical unless the flag is set. Client apps
> change by two links; SDKs are untouched.

## Decisions (from brainstorm)

- **Hosted on Duar, not in the SDKs.** Duar serves server-rendered HTML pages
  under `/onboard`. Apps link to them ("Sign up" / "Invite people"). No SDK or
  proxy allowlist changes.
- **One flag, one meaning.** `SELF_SERVE_ENABLED=false`. Off: `/onboard*` is
  404, proxy-mode `POST /workspaces` is 403 (it is open to any signed-in user
  today — a latent gap on the consortium instance, now closed), no invitation
  can exist. On: everything below.
- **Join first, create second.** The onboarding page leads with pending
  invitations and "have a code?"; creation is a deliberate secondary action.
  No JIT "Personal" workspace on sign-in — every drive-by login would mint a
  workspace, the user never chooses, and an invitee who signs in before
  redeeming would end up with two.
- **Consent is mandatory in self-serve mode.** Nobody can put a user into a
  workspace without that user's own POST. The legacy direct-add
  (`POST /workspaces/{id}/members/invite`, an email-existence oracle) returns
  403 while the flag is on. Duar-admin paths (admin panel add-user, CSV import)
  are unchanged on every instance — the Duar admin is trusted everywhere.
- **Two invitation kinds, one table.** Email-bound (appears automatically
  after the invitee signs in with that verified email) and bearer link
  (one-time code). Both single-use, 7-day TTL, never grant `owner`.
- **Anti-abuse is layered and cheap**: IdP-verified identity is the CAPTCHA
  (Google-only recommended on public instances), per-user creation cap,
  global creation velocity breaker, optional signup code as an emergency
  brake, and a `self_serve` marker on workspaces for later cleanup/quotas.
- **Identity for hosted pages = Duar's own OAuth client flow** (authlib code
  flow with `state`, same as proxy mode and admin login), ending in the
  existing signed session cookie. No new JWT audience, no IdP-token replay
  surface (the hosted flow never accepts a raw IdP token).
- **No JavaScript on hosted pages.** Jinja2 with autoescape, CSP
  `script-src 'none'`, plain forms. Smallest possible XSS surface for a page
  that strangers reach.

## Threat model

The trust shift: in self-serve mode a *workspace admin* is an untrusted
stranger, not a consortium administrator. Everything a workspace admin can do
to **other** people must require those people's action. Invariants:

| Invariant | Enforced by |
|---|---|
| Flag off ⇒ no new behavior | `/onboard*` 404, `POST /workspaces` 403, invitation service refuses when disabled |
| No consent-free membership | invitations only create membership on the invitee's authenticated POST; direct-add 403 under flag |
| Email-bound invite redeemable only by that email | compare `lower(user.email)` (IdP-verified) to `invitations.email`; mismatch ⇒ generic "invalid or expired" (no disclosure) |
| Bearer code unguessable, unreplayable | 32-byte `secrets.token_urlsafe`, SHA-256 at rest, atomic single-use claim |
| Invitation cannot escalate | `role ∈ {admin, editor, viewer}` (DB check); creator must be `owner`/`admin` of that workspace |
| Org allowlist holds | `assert_user_allowed_in_workspace` on every redeem (same gate as `invite_member`/`issue_tokens`) |
| No open redirect | `return_to` origin must be an active `ServiceApp.allowed_origins` entry or the origin of an active `ClientApp.redirect_uris` entry; otherwise 400 and nothing stored |
| Hosted session ≠ admin session | `/onboard` reads only the session cookie; `require_admin` reads only `admin_token`; neither grants the other |
| CSRF | session cookie is `SameSite=Lax` (no cross-site POST) **and** every form carries a session-bound token compared with `compare_digest` |
| Bounded creation | per-user cap (DB count) + global breaker (rate tier) + optional signup code |
| Audit | every create/accept/revoke writes an activity row and a `log_security` event |

Residual risks, accepted:

- **IdP email trust.** Email-bound invites are as strong as the IdP's
  `email_verified`. Google/GitHub: strict. Entra: only because the deployment
  pins one tenant (`auth_service.is_email_verified_claim`); a multi-tenant
  Entra issuer would let a rogue tenant claim any address — documented as a
  hard deployment constraint, unchanged from today.
- **One human, many IdP accounts** bypasses the per-user cap. Bounded by the
  global breaker and the Google-only recommendation (phone-verified accounts).
- **Spam invitations** can appear on a victim's onboarding page (name of a
  stranger's workspace). Duar sends no email, so there is no outbound channel;
  the inviter's name is shown; ≤ 50 pending per workspace; 7-day expiry.
- **Session cookie theft** yields ≤ 10 minutes of onboarding actions as that
  user (join/create/invite); `HttpOnly`, `Secure` in prod.
- **`/authz/resolve` rate bucket is per calling service** (`60/min`). Not
  changed here; public deployments raise `RATE_LIMIT_AUTHZ_RESOLVE` and rely
  on edge per-IP limiting, the documented posture for volumetric protection.

## 1. Configuration

```
SELF_SERVE_ENABLED=false                 # master switch
SELF_SERVE_MAX_WORKSPACES_PER_USER=1     # count of workspaces created_by user with self_serve=true
RATE_LIMIT_SELF_SERVE_CREATE="30/hour"   # instance-wide bucket for POST /onboard/create
SELF_SERVE_SIGNUP_CODE=""                # empty = open; set ⇒ creation requires it
```

- `rate_limit_self_serve_create` joins the `_validate_decorated_tier` list
  (non-empty, parseable).
- `/admin/system/settings` gains `self_serve: {enabled, max_workspaces_per_user,
  create_limit, signup_code_set}` — the code itself is never returned. The
  admin System Settings page shows one row.
- Invitation TTL (7 days) and pending cap (50/workspace) are constants.

## 2. Data model (one Alembic migration)

```
workspace_invitations
  id            uuid pk
  workspace_id  uuid fk workspaces(id) on delete cascade, not null
  email         text null            -- lowercased, as typed by the inviter
  code_hash     text null unique     -- sha256 hex of the bearer code
  role          text not null  check (role in ('admin','editor','viewer'))
  created_by    uuid fk users(id) on delete set null
  created_at    timestamptz not null default now()
  expires_at    timestamptz not null
  accepted_by   uuid fk users(id) on delete set null, null
  accepted_at   timestamptz null
  revoked_at    timestamptz null
  check ((email is null) <> (code_hash is null))          -- exactly one kind
  index ix_invitations_pending_email (email) where accepted_at is null and revoked_at is null
  index ix_invitations_workspace (workspace_id)

workspaces
  + self_serve  boolean not null default false
```

"Pending" ≡ `accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()`.

## 3. Service layer

`service/src/services/invitation_service.py`

- `create(db, *, workspace_id, role, created_by, actor_role, email=None) -> (Invitation, code | None)`.
  Refuses when the flag is off, when `actor_role ∉ {owner, admin}`, when
  `role == "owner"`, when the workspace already has 50 pending, or when
  `email` is malformed (exactly one `@`, ≤ 254 chars). `email` is lowercased.
  Without `email`, generates `secrets.token_urlsafe(32)`, stores its SHA-256,
  returns the plaintext once.
- `list_for_workspace(db, workspace_id)` — pending only.
- `list_pending_for_email(db, email)` — with workspace name and inviter name.
- `revoke(db, invitation_id, *, actor_id, actor_role)` — owner/admin of that
  workspace; sets `revoked_at`.
- `redeem(db, user, *, invitation_id=None, code=None) -> Workspace`.
  Exactly one selector. Looks up by id (then requires
  `invitation.email == lower(user.email)`) or by `sha256(code)`. Runs
  `assert_user_allowed_in_workspace`. Claims atomically:
  `UPDATE … SET accepted_by, accepted_at WHERE id = :id AND accepted_at IS NULL
  AND revoked_at IS NULL AND expires_at > now()`; rowcount ≠ 1 ⇒
  `InvitationInvalid` (one generic error for unknown / used / expired / revoked /
  wrong email). Inserts the membership; an existing membership is treated as
  success (idempotent) and the invitation is still consumed. Org rejection is
  its own error (it is about the user, safe to disclose).

`service/src/services/workspace_service.py`

- `create_self_serve(db, user, name, slug=None) -> Workspace`: cap check
  (`count(workspaces where created_by = user and self_serve)` ≥ cap ⇒
  `SelfServeCapReached`), slug = caller's if given (proxy-mode API) else
  `slugify(name)[:40] + "-" + secrets.token_hex(2)` where `slugify` =
  lowercase, every run of non-`[a-z0-9]` → `-`, trimmed of `-`, `"ws"` if
  empty (retry on collision, ≤ 3; a caller-supplied collision is a 409 as
  today), then `create_workspace(...)` with `self_serve=True`. Creator
  becomes owner as today.

Events (activity row + `log_security`, category `security`):
`workspace.self_serve.created`, `workspace.self_serve.denied`
(`reason=cap|signup_code|disabled`), `invitation.created`,
`invitation.accepted`, `invitation.revoked`, `invitation.rejected`
(`reason=invalid|org_not_allowed`).

## 4. Hosted routes — `service/src/api/onboard_routes.py`, prefix `/onboard`

All responses are HTML (`X-CSP-Override: html-page`; `_error_page` reused —
relocated to `src/api/html_pages.py` alongside the templates if the import
would be circular). Every route returns 404 when the flag is off. Session
keys: `onboard_user_id`, `onboard_csrf`, `onboard_return_to`, `onboard_code`,
`onboard_flash`, `onboard_result`.

| Route | Behavior |
|---|---|
| `GET /` | Resets onboarding session keys. Validates optional `?return_to` (see threat model) and stores it; stores optional `?code`. If already signed in ⇒ 302 `/onboard/home`; else provider buttons (configured providers only). |
| `GET /login/{provider}` | `rate_limit_auth` per IP. `authorize_redirect` to `{base_url}/onboard/callback/{provider}`. Unconfigured provider ⇒ 400 error page. |
| `GET /callback/{provider}` | `rate_limit_auth`. authlib `authorize_access_token`; profile via a helper **factored out of the existing proxy and admin callbacks** (`_idp_profile(client, token, provider)` → `provider_user_id, email, name, avatar_url, provider_data`, raising the same `email_not_verified` / no-email failures). Then: `resolve_organization` (None ⇒ 403 page, `_log_login_failure(..., "org_not_permitted", flow="onboard")`), `find_or_create_user` (`CrossProviderEmailConflict` ⇒ 409 page with the existing message), `is_active` (False ⇒ 403). Success: `auth.login.succeeded` with `flow="onboard"`, session `onboard_user_id` + fresh `onboard_csrf`, 302 `/onboard/home`. |
| `GET /home` | Requires session user (else 302 `/onboard`). Sections in order: (1) the link's code, if any — looked up; valid ⇒ "Join **{ws}** as {role}" card; invalid ⇒ inline notice, key cleared. (2) Pending email-bound invitations for the user's email — one card each with inviter name. (3) "Have an invite code?" — accepts a bare code or a full `/onboard?code=` link. (4) Workspaces the user is already in, with a Continue button. (5) "Create a workspace" — name field; signup-code field only when configured; replaced by "limit reached" text when at cap. |
| `POST /join` | `rate_limit_auth`. Form: `csrf`, and `invitation_id` xor `code`. `redeem`; success ⇒ `onboard_result` and 302 `/onboard/done`; `InvitationInvalid` ⇒ flash "This invitation is invalid, expired, or already used." ; org error ⇒ flash its message. |
| `POST /create` | `rate_limit_auth` per IP **and** the global `rate_limit_self_serve_create` bucket (stacked `@limiter.limit` decorators; if slowapi cannot stack, the breaker is a Redis `INCR`/`EXPIRE` in the handler). Form: `csrf`, `name` (SafeStr 1–255), `signup_code` (optional). Order: csrf → signup code (`compare_digest`; wrong ⇒ flash + `denied reason=signup_code`) → `create_self_serve` (cap ⇒ flash). 429 from the breaker renders an error page ("Too many workspaces are being created right now"). Success ⇒ 302 `/onboard/done`. |
| `GET /invites?workspace=` | Requires session user. Lists workspaces where the user's role ∈ {owner, admin}; preselects `?workspace` if the user qualifies there. For the selected workspace: pending invitations (email or "link", role, expires, Revoke button) and a create form: email (optional), role (viewer default / editor / admin). |
| `POST /invites` | `rate_limit_auth`. Form: `csrf`, `workspace_id`, `role`, `email?`. On success with email ⇒ flash "Invitation created — ask them to sign in at {base_url}/onboard". Without email ⇒ the link `{base_url}/onboard?code=…` is rendered **once** on the response (never retrievable again). |
| `POST /invites/{id}/revoke` | `csrf`; owner/admin of that invitation's workspace. |
| `GET /done` | Renders `onboard_result` ("You joined X" / "You created X"); "Continue to app" when `onboard_return_to` is set, otherwise "You're all set — go back to the app and sign in". Clears `onboard_code` and `onboard_result`; keeps the session so the user can go invite people. |
| `POST /logout` | Clears the session. Shown as a link on every signed-in page. |

Templates: `service/src/templates/onboard/{base,login,home,invites,done}.html`,
`Jinja2Templates(..., autoescape=True)`. `jinja2` becomes an explicit `service`
dependency (already in `uv.lock` transitively at 3.1.6). All hrefs and form
actions are absolute from `settings.base_url` so path-prefix deployments work.
Visuals: the `_error_page` card (dark, red header, system font); no external
assets.

### Legacy route changes

- `POST /workspaces` (proxy mode): `403 {"detail": "Workspace creation is disabled on this server"}` unless the flag is on. When on, it is a self-serve create like the hosted one: same per-user cap (`SelfServeCapReached` ⇒ 403), same global breaker (429), `self_serve=True`; only the slug stays caller-supplied. Otherwise a user holding one workspace could bypass every limit through the API.
- `POST /workspaces/{id}/members/invite`: 403 `{"detail": "Direct member add is disabled in self-serve mode; use invitations"}` when the flag is on.

## 5. App integration (what DocuStore does)

- "Sign up / Join a workspace" → `{DUAR_URL}/onboard?return_to={app origin path}`.
- "Invite people" (shown to owners/admins) → `{DUAR_URL}/onboard/invites?workspace={wid}&return_to=…`.
- After onboarding the user signs in again from the app; the IdP session is live so it is silent, and `/authz/resolve` (or `/auth/workspaces`) now lists the workspace. The SDKs' "No workspaces" error text is unchanged.

## 6. Deployment checklist (public instance)

1. `SELF_SERVE_ENABLED=true`; review the three sub-knobs.
2. Configure Duar's **own** IdP client (proxy-mode `GOOGLE_CLIENT_ID/SECRET`, …) and register `{BASE_URL}/onboard/callback/{provider}` at the IdP.
3. Prefer Google only. GitHub accounts are cheap for bots. Entra must stay single-tenant.
4. Register each public app's origin (ServiceApp `allowed_origins` / ClientApp `redirect_uris`) — that is the `return_to` allowlist.
5. Raise `RATE_LIMIT_AUTHZ_RESOLVE` (per-service bucket) and put per-IP edge rate limiting in front of Duar.
6. `COOKIE_SECURE=true`, `SESSION_SECRET_KEY` set (already required in prod).

## 7. Testing

Service suite (`TestClient` + dependency overrides for gates; SQLite-backed
sessions for the service layer, as the actions-dashboard tests do):

- Flag off: every `/onboard*` route 404; `POST /workspaces` 403; `invitation_service.create` refuses.
- Flag on: direct-add `members/invite` 403; admin add-user and CSV import unchanged.
- `redeem`: wrong email ⇒ generic invalid; expired / revoked / already used ⇒ generic invalid; two concurrent claims ⇒ exactly one succeeds; org-not-allowed ⇒ org error and invitation **not** consumed; already-member ⇒ success, invitation consumed.
- `create`: `role=owner` rejected; non-admin actor rejected; 51st pending rejected; email lowercased; code returned once and only its hash stored.
- `create_self_serve`: cap enforced by `created_by`+`self_serve`; slug collision retry; `self_serve=True`; creator is owner.
- Hosted: `return_to` off-allowlist ⇒ 400 and not stored; on-allowlist ⇒ rendered on done; POST without/with wrong csrf ⇒ 403; signup code wrong ⇒ denied event; breaker ⇒ 429 page; `GET` never mutates (join/create only via POST).
- Session isolation: an `admin_token` cookie alone does not authenticate `/onboard/home`; an onboarding session does not authenticate `/admin/*`.
- Config: `RATE_LIMIT_SELF_SERVE_CREATE=""` rejected at startup.
- One manual browser click-through on localhost (Google SSO): create, invite by email from a second account, accept, invite by link, revoke, continue-to-app.

## 8. Docs and changelog

- `docs/guide/self-serve.md`: concept, both flows with screenshots-free
  step lists, the app integration snippet, the deployment checklist, the
  threat model summary. Nav entry in `mkdocs.yml`.
- Configuration reference rows for the four settings; `docs/security.md`
  paragraph on the flag and the consortium-tightening.
- `CHANGELOG.md` 1.3.0 (Unreleased): feature entry + **behavior change**:
  proxy-mode `POST /workspaces` now requires `SELF_SERVE_ENABLED`.

## 9. Non-goals (v1)

SDK changes (a later 1-liner may add `onboard_url` to resolve discovery);
JSON invitation API for apps that want native UI; admin-panel invitation
views or a self-serve filter (the column makes it a later 1-liner; admins can
already delete workspaces); invitee "decline"; "leave workspace"; approval
queue; dormant-workspace cleanup; multi-use codes; a token claim for
self-serve tier; Duar sending email.
