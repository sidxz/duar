# Security

Duar is production-hardened with defense-in-depth across transport, authentication, token lifecycle, and input validation. This page documents the full security architecture.

---

## Middleware Stack

Middleware is applied in a specific order. The outermost layer processes requests first:

```
Request
  |
  v
MaxBodySizeMiddleware        -- reject bodies > 10 MB
  |
  v
SecurityHeadersMiddleware    -- security response headers + HSTS
  |
  v
SessionMiddleware            -- encrypted cookie for OAuth2 state
  |
  v
TrustedHostMiddleware        -- Host header validation (production)
  |
  v
DynamicCORSMiddleware        -- cross-origin request policy
  |
  v
Rate Limiting (slowapi)      -- aggregate + per-endpoint tier throttling
  |
  v
Application Routes
```

Source: `service/src/main.py`

---

## Security Headers

Every response includes 11 security headers set by `SecurityHeadersMiddleware`:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Content-Type-Options` | `nosniff` | Prevents MIME type sniffing |
| `X-Frame-Options` | `DENY` | Blocks clickjacking via iframes |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limits referrer leakage |
| `X-XSS-Protection` | `0` | Disables legacy XSS filter (CSP preferred) |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | Restricts browser APIs |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'` | Blocks all resource loading and framing |
| `Cross-Origin-Embedder-Policy` | `require-corp` | Prevents cross-origin resource leaks |
| `Cross-Origin-Opener-Policy` | `same-origin` | Isolates browsing context |
| `Cross-Origin-Resource-Policy` | `same-origin` | Restricts resource sharing |
| `X-Permitted-Cross-Domain-Policies` | `none` | Blocks Flash/PDF cross-domain access |
| `Server` | `daikon` | Masks underlying server technology |

Sensitive paths (`/auth`, `/admin`, `/users`) additionally receive `Cache-Control: no-store` and `Pragma: no-cache`.

When `COOKIE_SECURE=true`, HSTS is enabled:

```
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
```

This enforces HTTPS for two years and is eligible for the [HSTS preload list](https://hstspreload.org/).

Source: `service/src/middleware/security_headers.py`

---

## Authentication Tiers

Four tiers, applied by endpoint sensitivity:

| Tier | Mechanism | Use Case | Example Endpoints |
|------|-----------|----------|-------------------|
| **User JWT** | `Authorization: Bearer <token>` | End-user actions | `/users/me`, `/workspaces`, `/groups` |
| **Service Key + JWT** | `X-Service-Key` + `Authorization: Bearer` | Service acting on behalf of a user | `/permissions/check`, `/permissions/accessible` |
| **Service Key Only** | `X-Service-Key` header | Autonomous service operations | `/permissions/register`, `/permissions/visibility` |
| **Admin Cookie** | `admin_token` HttpOnly cookie | Admin panel operations | `/admin/*` |

Service keys are database-managed (`service_apps` table), not environment variables. Each key is stored as a SHA-256 hash with a display prefix (e.g., `sk_abc1****`). Keys are validated by `service_app_service.validate_key()` with Redis caching.

---

## JWT Security

### RS256 Signing

All tokens use RS256 (RSA + SHA-256). The private key signs tokens; any service with the public key can verify without contacting Duar.

```bash
openssl genrsa -out keys/private.pem 2048
openssl rsa -in keys/private.pem -pubout -out keys/public.pem
```

The public key is also available at `/.well-known/jwks.json`.

### Key Rotation

Every token carries a `kid` (RFC-7638 thumbprint) header, and JWKS publishes the
current key plus any retired keys (`JWT_PREVIOUS_PUBLIC_KEY_PATHS`). Verifiers
select the key by `kid`, so a new key can sign while old tokens still verify —
graceful, zero-downtime rotation. SDK middlewares refetch JWKS on an unknown
`kid`. See the runbook: [deployment/key-rotation.md](deployment/key-rotation.md).

### Audience Separation

Tokens carry a `type` claim that prevents cross-use:

| Token Type | `type` Claim | Audience |
|------------|-------------|----------|
| Access token | `access` | `sentinel:access` |
| Refresh token | (Redis only) | N/A |
| Admin token | `admin_access` | `sentinel:admin` |
| Authz token | `authz` | `sentinel:authz` |

### Token ID (`jti`)

Every token includes a `jti` (UUID). This enables per-token revocation via the Redis denylist without invalidating all tokens for a user.

### Access Token Claims

| Claim | Type | Description |
|-------|------|-------------|
| `sub` | UUID | User ID |
| `jti` | UUID | Token identifier (for revocation) |
| `email` | string | User email |
| `name` | string | Display name |
| `wid` | UUID | Workspace ID |
| `wslug` | string | Workspace slug |
| `wrole` | string | Workspace role (`owner`/`admin`/`editor`/`viewer`) |
| `groups` | UUID[] | Group IDs in this workspace |
| `type` | string | `"access"` |
| `iat` / `exp` | timestamp | Issued at / expiration |

### Token Lifetimes

| Token | Default | Config Variable |
|-------|---------|-----------------|
| Access | 15 minutes | `ACCESS_TOKEN_EXPIRE_MINUTES` |
| Refresh | 7 days | `REFRESH_TOKEN_EXPIRE_DAYS` |
| Admin | 1 hour | `ADMIN_TOKEN_EXPIRE_MINUTES` |
| Authz | 5 minutes | `AUTHZ_TOKEN_EXPIRE_MINUTES` |

---

## Token Lifecycle

### Refresh Token Rotation

Modeled after [Auth0's refresh rotation](https://auth0.com/docs/secure/tokens/refresh-tokens/refresh-token-rotation):

1. **Issuance** -- on authentication, the service issues an access + refresh token pair. The refresh token's `jti` is stored in Redis with a `family_id`.
2. **Rotation** -- `POST /auth/refresh` atomically consumes the token (`GETDEL`), issues a new pair in the same family.
3. **Reuse detection** -- a consumed token presented again signals theft. The entire family is revoked.
4. **Family revocation** -- on theft detection or user deactivation, all `jti` entries in the family set are deleted.

Redis keys:

| Key | Value | TTL |
|-----|-------|-----|
| `rt:{jti}` | `{user_id}:{family_id}` | `REFRESH_TOKEN_EXPIRE_DAYS` |
| `rtf:{family_id}` | Set of `jti` values | `REFRESH_TOKEN_EXPIRE_DAYS` |

### Authorization Codes

After OAuth callback, Duar issues a short-lived authorization code (not raw user IDs in redirects):

1. Frontend sends `code_challenge` + `code_challenge_method=S256` on `GET /auth/login/{provider}`
2. Callback stores the code in Redis with a 5-minute TTL alongside the `code_challenge`
3. `POST /auth/workspaces` (body: `code` + `code_verifier`) verifies PKCE, then peeks at the code (non-destructive)
4. `POST /auth/token` verifies `SHA256(code_verifier) == code_challenge`, then consumes the code via `GETDEL`

Redis key: `ac:{code}` -- JSON `{user_id, provider, code_challenge, code_challenge_method}`, 5-minute TTL.

### Access Token Revocation

On logout, the `jti` is added to a Redis denylist with TTL equal to the token's remaining lifetime. Every authenticated request checks the denylist. Entries self-expire when the token would have expired anyway.

Redis key: `bl:{jti}` -- value `"1"`, TTL = remaining seconds.

### Logout Completeness

`POST /auth/logout` performs two actions:

1. Blacklists the access token (`jti` to denylist)
2. Revokes all refresh token families for the user (`revoke_all_user_tokens`)

This prevents an attacker with a captured refresh token from obtaining new access tokens after the user logs out.

---

## Cookie Security

Admin cookies are configured for defense in depth:

| Attribute | Value | Purpose |
|-----------|-------|---------|
| `httponly` | `True` | Prevents JavaScript access (XSS mitigation) |
| `samesite` | `strict` | Blocks cross-site request inclusion |
| `secure` | `COOKIE_SECURE` | Restricts to HTTPS when enabled |
| `max_age` | `3600` | Expires after 1 hour |
| `path` | `/` | Available across all routes |

### CSRF Protection

Two layers:

1. **SameSite=Strict** -- the `admin_token` cookie is never sent on cross-site requests.
2. **Custom header** -- all mutation requests (POST/PATCH/PUT/DELETE) to `/admin/*` require `X-Requested-With: XMLHttpRequest`. This header cannot be set by cross-origin forms, and CORS preflight blocks cross-origin JavaScript from adding it.

### Session Cookies

`SessionMiddleware` provides encrypted session cookies used exclusively during OAuth2 flows. The session stores `state` and PKCE `code_verifier` between redirect and callback. Sessions expire after 10 minutes (`max_age=600`).

```bash
# Generate a session secret (required for production)
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Rate Limiting

[slowapi](https://github.com/laurents/slowapi) is the sole rate-limiting mechanism. It applies two complementary layers via Redis-backed per-IP counters:

1. **Aggregate** (`RATE_LIMIT_AGGREGATE`, default `300/minute`) — a volumetric ceiling applied to every route that does **not** carry a per-route decorator. Provides baseline abuse prevention for undecorated read paths.
2. **Per-route tiers** — decorated endpoints enforce a more specific limit from the tier set below. The aggregate does **not** additionally apply to these routes.

| Tier | Env var | Default | Covered endpoints |
|------|---------|---------|-------------------|
| Aggregate | `RATE_LIMIT_AGGREGATE` | `300/minute` | All undecorated routes |
| Default | `RATE_LIMIT_DEFAULT` | `120/minute` | Each undecorated route (per-endpoint) |
| Auth | `RATE_LIMIT_AUTH` | `10/minute` | `/auth/login`, `/auth/callback`, `/auth/token`, `/auth/refresh` |
| Auth admin | `RATE_LIMIT_AUTH_ADMIN` | `5/minute` | `/auth/admin/login`, `/auth/admin/callback` |
| Authz resolve | `RATE_LIMIT_AUTHZ_RESOLVE` | `60/minute` | `POST /authz/resolve` |
| Read | `RATE_LIMIT_READ` | `60/minute` | Member search, group listing, enriched ACL |
| Admin write | `RATE_LIMIT_ADMIN_WRITE` | `10/minute` | Admin mutation endpoints |
| Sensitive | `RATE_LIMIT_SENSITIVE` | `5/minute` | Service purge, key-rotation |

Keys are per IP address. When `BEHIND_PROXY=true`, the client IP is read from `X-Forwarded-For` using the rightmost trusted hop (controlled by `TRUSTED_PROXY_COUNT`). Exceeding any limit returns `429 Too Many Requests` with a `Retry-After` header.

If Redis is unreachable the limiter **fails open** (requests are not blocked). This is intentional for availability; edge rate limiting should be the backstop for volumetric attacks.

> **Volumetric DoS protection requires edge rate limiting.** The app-layer aggregate (`RATE_LIMIT_AGGREGATE`) only covers routes without a per-route limit; decorated routes (`/authz/resolve`, `/auth/token`, admin POSTs) enforce their own tier but are **not** in the aggregate and are **not** throttled before authentication. Put nginx/Cloudflare/ALB rate limiting in front of Duar for volumetric/bad-auth defense.

To disable rate limiting entirely (e.g. in a dedicated load-test environment), set `RATE_LIMIT_ENABLED=false`.

---

## OAuth Hardening

### PKCE (S256)

PKCE prevents authorization code interception. Duar uses S256 where supported:

| Provider | PKCE | Notes |
|----------|------|-------|
| Google | S256 | Full OIDC |
| Microsoft Entra ID | S256 | Full OIDC |
| GitHub | No | Does not support PKCE; relies on `state` |

PKCE is configured at the Authlib client registration level. Authlib generates `code_verifier` and `code_challenge` automatically.

### Client App Allowlist + `client_id` Binding

Applications must be registered as client apps before using Duar. `GET /auth/login/{provider}` requires a `client_id` query parameter naming the ClientApp initiating the flow. Duar validates that the supplied `redirect_uri` is listed on **that specific** app's `redirect_uris` — not any active app. The `client_id` is stored in the session and re-verified on callback before an auth code is issued.

This prevents authorization-code interception where an attacker crafted a login URL with their own `code_challenge` and another app's `redirect_uri`, then redeemed the resulting code against their own verifier. Without `client_id` binding, a single compromised or attacker-controllable redirect URI anywhere in the allowlist would have been enough; with it, the `(client_id, redirect_uri)` pair must match.

### Redirect URI Validation

Redirect URIs (and ServiceApp origins) are parsed with `urlparse` and must have:

- Scheme `http` or `https`
- Non-empty host
- No `@` userinfo, no fragment, no query (URIs); no path/query/fragment (origins)
- No wildcards, no `"null"`, no malformed round-trip

Stricter than a prefix check — rejects values like `https://good@evil.com/cb` or `https://` at write time.

### State Parameter

All OAuth2 flows use `state` (managed by Authlib via `SessionMiddleware`) to prevent CSRF during authorization code exchange.

### CORS

`DynamicCORSMiddleware` combines two origin sources:

1. **Static** -- from `CORS_ORIGINS` environment variable
2. **Dynamic** -- derived from `client_apps.redirect_uris` in the database

Policy: credentials enabled, methods `GET/POST/PUT/PATCH/DELETE/OPTIONS`, headers `Content-Type`, `Authorization`, `X-Service-Key`. No wildcards in production.

---

## AuthZ Mode Security

AuthZ mode lets client apps authenticate users directly with their IdP and hand the resulting token to Duar, which issues a short-lived authorization JWT. Several defences ensure the IdP remains the sole authentication authority.

### IdP Audience + Issuer Enforcement

Both the Python and Next.js AuthZ middlewares require you to configure:

- `idp_audience` — the OAuth client_id this app is registered as. The IdP token's `aud` must match.
- `idp_issuer` — optional but strongly recommended; the IdP token's `iss` must match.

Without `aud` validation, any token signed by the IdP for any OAuth client would authenticate — including one minted for an attacker's app. Duar's server-side `/authz/resolve` has always enforced audience; the middleware change brings client verification to parity.

### Authz Token Bindings

The authz JWT carries three binding claims that the middleware enforces on every request:

| Claim | Bound to | Enforcement |
|-------|----------|-------------|
| `aud` | `sentinel:authz` | JWT decode |
| `idp_sub` | IdP token's `sub` | Middleware asserts equality (both non-empty) |
| `svc` | Calling service's `service_name` | Middleware asserts equality — stops cross-service token replay |

Server-side dependencies (`get_user_for_service_call`, `get_current_user_flexible`) enforce the same `svc` binding when authz tokens are accepted alongside a service key.

### Nonce Replay Protection

`POST /authz/resolve` accepts an optional `nonce` field. When provided, the IdP token's `nonce` claim must match. Browsers generate a nonce at login-start and echo it here — a leaked IdP token cannot be replayed without the matching nonce.

### AuthZ-mode Redirect Allowlist

`GET /authz/idp/github/login` takes a caller-supplied `redirect_uri`. Duar validates the URI's origin (scheme + host + port) against `ServiceApp.allowed_origins` — attackers cannot point the GitHub proxy at their own domain to exfiltrate the access token. The allowlist is re-checked on callback.

### Cross-Provider Email Linking — Disabled

`find_or_create_user` keys identity strictly on `(provider, provider_user_id)` and never auto-links across providers by email. A new IdP login whose email matches a user who already has a SocialAccount under a **different** provider is **rejected with `409 CrossProviderEmailConflict`** — attackers with a weaker IdP cannot take over an account created under a stronger one. An email match against an admin-pre-provisioned account that has **no** SocialAccount yet is the intended first sign-in and is linked to that account, so pre-provisioning works without locking the user out.

### Revocation for Authz Tokens

Authz tokens carry `jti` and `sub`. **On Duar's own endpoints** — the
`get_user_for_service_call` and `get_current_user_flexible` dependencies — they go
through the same revocation checks as access tokens on every request:

- `jti` on the denylist → 401
- `sub` marked deactivated → 401

So admin-driven deactivation takes effect immediately for any request that reaches Duar.

**SDK edge (important).** The `AuthzMiddleware` shipped in the SDK and installed via
`duar.protect(app)` validates tokens **offline** — signature, audience, expiry,
and the `idp_sub`/`svc` bindings — and deliberately does **not** call back to Duar
to consult the denylist or deactivation flag (no network round-trip per request).
The consequence: at an SDK-protected downstream service, a deactivated user's
already-issued authz token stays accepted until it **expires naturally**.

Authz tokens are not enrolled in a refresh family, so the deactivation flag is their
only revocation lever — and it is consulted only on Duar's own endpoints. Bound
the exposure by keeping `AUTHZ_TOKEN_EXPIRE_MINUTES` short (default 5). For
revocation-sensitive operations at a downstream service, route the decision through
Duar (e.g. a `PermissionClient` / `RoleClient` check) rather than relying on the
offline middleware alone. (A future opt-in revocation check in the middleware could
close this at the cost of a per-request network call.)

### Browser Storage (`AuthzLocalStorageStore`)

The JS SDK's persistent store keeps the short-lived authz token in `localStorage` but **not** the long-lived IdP token — that stays in instance memory only. After a page reload the SDK has no IdP token, so `getAuthState()` reports `needs_reauth` (`isAuthenticated` is `false`) and the user re-authenticates via the IdP — either with one click, or seamlessly via `silentLogin()` (`prompt=none`). This limits the XSS blast radius: an attacker reading `localStorage` gets a token that expires in minutes, not an IdP token that continues to mint new authz tokens for the next hour. The surviving authz token is not independently usable — the backend binds it to a freshly-signed IdP token (`idp_sub` must match), which is never persisted.

### Nonce on the Callback

`DuarAuthz.handleCallback()` fails closed if `sessionStorage` does not contain a nonce from a login this tab initiated. This blocks login-CSRF where an attacker links a victim to `/auth/callback#id_token=<attacker_token>` to hijack the session.

### Admin Stale-Privilege Protection

`require_admin` re-checks `users.is_active` and `users.is_admin` against the database on every request. Flipping an admin's flag takes effect on the next request, not only after the cookie expires.

### Minting Requires a Service Key

`POST /authz/resolve` differentiates two trust levels:

- **Discovery** (no `workspace_id`, returns the user's workspace list) — accepts either `X-Service-Key` or a registered `Origin` header. No credential issued, low sensitivity.
- **Minting** (with `workspace_id`, returns a signed authz JWT) — **requires `X-Service-Key`**. Origin-authenticated callers are rejected with `403`. Credential issuance is a server-to-server trust step.

Browsers therefore cannot call `/authz/resolve` to mint tokens. The `DuarAuthz` SDK routes minting through a `mintEndpoint` on the downstream app's backend, which holds the service key. This closes the "XSS-window extension" attack where an attacker with a fleeting XSS could keep re-minting authz tokens for the IdP token's full TTL (~1 hour for Google). Post-fix, an XSS is bounded to replaying the one authz token already in memory (5-min TTL).

---

## Input Validation

### Pydantic Schemas

All request bodies are validated with Pydantic models. Invalid input is rejected with `422` before reaching business logic. This covers type checking, UUID format validation, enum constraints, and required/optional field enforcement.

### Request Body Size

`MaxBodySizeMiddleware` rejects requests exceeding 10 MB (`413 Request Entity Too Large`). It checks both `Content-Length` headers and actual streamed bytes to catch chunked uploads. CSV import endpoints enforce a stricter 5 MB limit at the application level.

### Action Name Validation

RBAC action names must match `^[a-z][a-z0-9_.:-]*$`, namespaced by `service_name`.

---

## Startup Checks (Fail-Closed)

When `DEBUG=false`, the service refuses to start if any check fails:

| Check | Failure Condition | Error |
|-------|-------------------|-------|
| Session secret | Default value unchanged | `SESSION_SECRET_KEY is using the default dev value` |
| Service apps | No active apps in DB | `No active service apps registered` |
| Cookie security | `COOKIE_SECURE=false` | `COOKIE_SECURE is False` |
| Redis connectivity | Cannot ping | `Redis is unreachable` |
| Redis auth | No `@` in `REDIS_URL` | `Redis URL has no authentication` |
| Allowed hosts | Resolved to `*` | `ALLOWED_HOSTS is wildcard` |

Redis TLS and certificate verification are checked separately and logged as warnings (not blocking).

In development (`DEBUG=true`), all checks are logged as warnings instead.

---

## Trusted Host Validation

When `ALLOWED_HOSTS` resolves to anything other than `*`, `TrustedHostMiddleware` validates the `Host` header on every request. This prevents Host header injection attacks used in cache poisoning.

If `ALLOWED_HOSTS` is empty, hostnames are derived from `BASE_URL` and `ADMIN_URL`.

---

## SDK Insecure URL Warnings

All SDK clients (Python and JavaScript) log a warning when initialized with a plain `http://` URL not pointing to localhost. This catches accidental production deployments without HTTPS.

| SDK | Warning |
|-----|---------|
| Python (`duar-auth`) | `logging.warning()` via `duar_auth` logger |
| JS (`@duar-auth/js`) | `console.warn()` |
| Next.js (`@duar-auth/nextjs`) | `console.warn()` in `createDuarMiddleware` |

---

## Production Checklist

- [ ] `SESSION_SECRET_KEY` set to a cryptographically random value
- [ ] `COOKIE_SECURE=true` and service is behind TLS
- [ ] `DEBUG=false`
- [ ] `BEHIND_PROXY=true` if behind a reverse proxy
- [ ] `TRUSTED_PROXY_COUNT` matches actual proxy hop count (default `1`; verify per deploy)
- [ ] Edge rate limiting (nginx/Cloudflare/ALB) configured in front of Duar for volumetric/bad-auth defense
- [ ] `ALLOWED_HOSTS` set to actual domain(s)
- [ ] `CORS_ORIGINS` lists only your frontend origin(s)
- [ ] RS256 key pair generated, private key `chmod 600`
- [ ] At least one service app registered via admin panel
- [ ] PostgreSQL uses SSL (`?ssl=require` in `DATABASE_URL`)
- [ ] Redis uses TLS (`rediss://`), has a strong password, not publicly exposed
- [ ] `REDIS_TLS_VERIFY=required` with CA cert configured
- [ ] `ADMIN_EMAILS` set for auto-promotion
- [ ] TLS certs generated for Postgres and Redis (`keys/tls/`)
- [ ] Reverse proxy handles TLS termination and sets `X-Forwarded-For`
- [ ] Startup validation passes with `DEBUG=false`
