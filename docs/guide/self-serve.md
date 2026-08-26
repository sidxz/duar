# Self-serve workspaces

Public-facing deployments (a demo of an app anyone can sign into with Google or
GitHub) need users with **no** workspace to get one without a Duar admin in the
loop. With `SELF_SERVE_ENABLED=true`, Duar hosts onboarding pages under
`/onboard` where a user **joins** a workspace through a one-time invitation
link (the encouraged path) or **creates** one and becomes its owner.

Everything is off by default. Consortium/enterprise deployments leave the flag
unset; the only *behavioral* change they see in this release is that
proxy-mode `POST /workspaces` now returns `403` (it was open to any signed-in
user) — the CSP header and the `/admin/system/settings` block also change, as
the changelog notes.

## How it works

- Pages are server-rendered by Duar (no JavaScript) and use Duar's **own**
  IdP client — the same code flow proxy mode and the admin panel use. Sign-in
  audits and security signals fire exactly as for any login.
- **Invitations** are one-time links minted by a workspace owner/admin on
  `/onboard/invites`. A link is shown once, expires after 7 days, can grant
  `viewer`, `editor`, or `admin` (never `owner`), can optionally be locked to
  one email address, and is only valid while the inviter is still an
  owner/admin of that workspace. Nobody joins a workspace without their own
  click — the legacy direct-add endpoint is disabled in self-serve mode.
- **Creation** is capped per user (`SELF_SERVE_MAX_WORKSPACES_PER_USER`,
  `0` = join-only) and instance-wide per hour
  (`SELF_SERVE_MAX_CREATES_PER_HOUR`); slugs are generated.

## Flows

**Invitee with a link** — opens `{DUAR}/onboard?code=…` → signs in → "Join
*Workspace* as *role*" → **Join** → **Continue to app** (when the link carries
a `return_to`) → signs into the app (silent; the IdP session is live).

**New user, no invite** — app's "Sign up" link → `{DUAR}/onboard?return_to=…`
→ signs in → **Create a workspace** → **Continue to app**.

**Workspace admin** — app's "Invite people" link →
`{DUAR}/onboard/invites?workspace={id}&return_to=…` → role (+ optional email
lock) → **Create invite link** → copy the link, send it however you like.
Duar does not send email.

**Workspace admin — manage members** — the same `Manage workspaces` page lists
the workspace's members with their roles: change a role (only owners can grant
or demote `owner`; the last owner can never be demoted), remove a member
(their live app sessions are revoked), or rename the workspace. Any member can
leave a workspace from the `/onboard/home` list — except its last owner.

## App integration

Two links; no SDK changes:

```html
<a href="https://duar.example/onboard?return_to=https://app.example/login">Sign up / Join a workspace</a>
<a href="https://duar.example/onboard/invites?workspace=WORKSPACE_ID&return_to=https://app.example/login">Invite people</a>
```

`return_to` must be on an origin registered in the app's Service App
`allowed_origins` (the same list AuthZ CORS uses) and should be a page that
**starts sign-in**: after onboarding the app has no session yet. In AuthZ mode
call `silentLogin('google')` with an explicit provider (the SDK forgets the
provider once a zero-workspace callback has been handled) or show a
"Sign in" button.

Show the "Sign up" link permanently on the login page and make your
`errorComponent` say "No workspace yet — sign up" for the zero-workspace error.

## Deployment checklist

1. `SELF_SERVE_ENABLED=true`; review `SELF_SERVE_MAX_WORKSPACES_PER_USER` and
   `SELF_SERVE_MAX_CREATES_PER_HOUR`.
2. An **enabled public organization** must exist (it does by default) —
   otherwise unclaimed email domains cannot sign in at all.
3. Configure Duar's own IdP client: `GOOGLE_CLIENT_ID` **and**
   `GOOGLE_CLIENT_SECRET`, and register `{BASE_URL}/onboard/callback/google`
   as a redirect URI on the same Google OAuth client the app uses.
4. Prefer Google only: GitHub accounts are cheap for bots. Entra is not
   recommended on a public instance; if used it must be single-tenant and the
   app registration must emit `xms_edov` — onboarding sign-in fails with 403
   unless the claim is present.
5. `TIER` must include the public listener (`public` or `all`).
6. Raise `RATE_LIMIT_AUTHZ_RESOLVE` (one bucket per calling service) and put
   per-IP rate limiting at the edge; edge logs will contain single-use
   `?code=` URLs.
7. `COOKIE_SECURE=true` and a real `SESSION_SECRET_KEY`.

## Security model

Session cookie is `SameSite=Lax`, `HttpOnly`, 10-minute sliding window; every
form carries a CSRF token; pages are `Cache-Control: no-store`; `return_to` is
allowlisted; invite codes are 256-bit, stored hashed, claimed by an atomic
single-use update; invitations die with the inviter's standing; org
allowlists are enforced on redemption; every create/accept/revoke is written
to the activity log and the security event stream. Full analysis:
`docs/superpowers/specs/2026-08-25-self-serve-workspaces-design.md`.
