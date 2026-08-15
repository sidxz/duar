# Configuration Reference

All configuration is via environment variables, loaded from `service/.env` (dev) or `.env.prod` (production). The service uses [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) with `env_file=".env"`.

```bash
# make setup generates both files automatically
cp .env.dev.example service/.env     # local development
cp .env.prod.example .env.prod       # production deployment
```

---

## Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://identity:identity_dev@localhost:9001/identity?ssl=require` | Async PostgreSQL connection string. Must use the `asyncpg` driver. |
| `REDIS_URL` | `rediss://:duar_dev@localhost:9002/0` | Redis connection string. Use `rediss://` for TLS. |
| `REDIS_TLS_CA_CERT` | `""` | Path to CA cert for Redis TLS verification (e.g. `keys/tls/ca.crt`). |
| `REDIS_TLS_VERIFY` | `none` | `"none"` or `"required"`. Set to `"required"` in production. |

The defaults match the `docker-compose.yml` dev configuration.

---

## JWT

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_PRIVATE_KEY_PATH` | `keys/private.pem` | RSA private key for signing tokens. |
| `JWT_PUBLIC_KEY_PATH` | `keys/public.pem` | RSA public key for verifying tokens. |
| `JWT_ALGORITHM` | `RS256` | Signing algorithm. Only RS256 is supported. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token lifetime. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime. |
| `ADMIN_TOKEN_EXPIRE_MINUTES` | `60` | Admin panel session token lifetime. |
| `AUTHZ_TOKEN_EXPIRE_MINUTES` | `5` | AuthZ mode token lifetime. Short by design -- the JS SDK refreshes automatically. |

Generate keys manually (or let `make setup` handle it):

```bash
mkdir -p keys
openssl genrsa -out keys/private.pem 2048
openssl rsa -in keys/private.pem -pubout -out keys/public.pem
```

---

## OAuth Providers

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CLIENT_ID` | `""` | Google OAuth 2.0 client ID. |
| `GOOGLE_CLIENT_SECRET` | `""` | Google OAuth 2.0 client secret. |
| `GITHUB_CLIENT_ID` | `""` | GitHub OAuth app client ID. |
| `GITHUB_CLIENT_SECRET` | `""` | GitHub OAuth app client secret. |
| `ENTRA_CLIENT_ID` | `""` | Microsoft Entra ID application client ID. |
| `ENTRA_CLIENT_SECRET` | `""` | Microsoft Entra ID application client secret. |
| `ENTRA_TENANT_ID` | `""` | Microsoft Entra ID tenant ID. Required when using Entra. |

A provider is enabled when both its client ID and secret are set. You can enable multiple providers simultaneously. At least one is required.

**Callback URLs** -- register these with your IdP when creating the OAuth application:

| Provider | Redirect URI |
|----------|-------------|
| Google | `{BASE_URL}/auth/callback/google` |
| GitHub | `{BASE_URL}/auth/callback/github` |
| Entra ID | `{BASE_URL}/auth/callback/entra_id` |

The provider identifier is `entra_id` (not `entra`) everywhere: callback paths, the `provider` field on `POST /authz/resolve`, and `GET /auth/providers`.

These callbacks are only used for the admin panel's server-side OAuth flow. In AuthZ mode, your frontend authenticates directly with the IdP.

!!! note "Entra ID: email claim"
    Entra omits the `email` claim for managed work accounts unless the app registration adds it as an **optional claim** (Token configuration → Add optional claim → ID → `email`). Without it Duar falls back to `preferred_username` (the UPN) when that is address-shaped; if neither is present, sign-in is refused with an actionable error. Entra also emits no `email_verified` claim — see [Authentication](../guide/authentication.md#supported-idps).

---

## Service

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_HOST` | `0.0.0.0` | Host address the service binds to. |
| `SERVICE_PORT` | `9003` | Port the service listens on. |
| `BASE_URL` | `http://localhost:9003` | Public URL of Duar. Used for OAuth callback URLs and JWKS endpoint. |
| `FRONTEND_URL` | `http://localhost:3000` | Default frontend URL for post-login redirects. |

---

## Security

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_SECRET_KEY` | `dev-only-change-me-in-production` | **Required in production.** Secret for signing server-side sessions during OAuth flows. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:9101` | Comma-separated CORS origins. Combined with origins from registered service apps at runtime. |
| `COOKIE_SECURE` | `false` | Set `true` in production (requires HTTPS). |
| `ALLOWED_HOSTS` | `""` | Comma-separated hostnames. Empty = derived from `BASE_URL` + `ADMIN_URL`. |
| `DEBUG` | `false` | Enables `/docs` and `/redoc` Swagger UI. Relaxes startup validation. |
| `BEHIND_PROXY` | `false` | Set `true` behind a reverse proxy (nginx, ALB). Enables `X-Forwarded-For` for rate limiting. |
| `TRUSTED_PROXY_COUNT` | `1` | Number of trusted reverse-proxy hops in front of Duar. Must match your actual topology (e.g. `2` for CDN + nginx). Incorrect values collapse all clients into one IP bucket. |

---

## Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_ENABLED` | `true` | Master switch. Set `false` to disable all rate limiting (e.g. load-test environments). |
| `RATE_LIMIT_AGGREGATE` | `300/minute` | Per-IP volumetric ceiling applied to routes **without** a per-route limit. Empty string disables the aggregate. See the edge-limiting note in [Security](../security.md#rate-limiting). |
| `RATE_LIMIT_DEFAULT` | `120/minute` | Per-IP limit applied to each **undecorated** route individually (the long tail); the cross-route ceiling is `RATE_LIMIT_AGGREGATE`. |
| `RATE_LIMIT_AUTH` | `10/minute` | Auth flow endpoints (`/auth/login`, `/auth/callback`, `/auth/token`, `/auth/refresh`). |
| `RATE_LIMIT_AUTH_ADMIN` | `5/minute` | Admin auth endpoints (`/auth/admin/login`, `/auth/admin/callback`). |
| `RATE_LIMIT_AUTHZ_RESOLVE` | `60/minute` | `POST /authz/resolve` — authz token minting. |
| `RATE_LIMIT_READ` | `60/minute` | Read-heavy endpoints (member search, group listing, enriched ACL). |
| `RATE_LIMIT_ADMIN_WRITE` | `10/minute` | Admin mutation endpoints (POST/PATCH/PUT/DELETE under `/admin/`). |
| `RATE_LIMIT_SENSITIVE` | `5/minute` | High-sensitivity endpoints (service purge, key rotation). |

**Production checklist:**

- `SESSION_SECRET_KEY` -- unique random value (not the default)
- `COOKIE_SECURE=true` -- deployment must use HTTPS
- `ALLOWED_HOSTS` -- your actual domain(s)
- `CORS_ORIGINS` -- your frontend domain(s) only
- `REDIS_TLS_VERIFY=required`
- `DEBUG=false`
- `BEHIND_PROXY=true` + `TRUSTED_PROXY_COUNT` matching the real proxy hop count (default `1`; verify per deploy -- a wrong value collapses all clients into one rate-limit bucket)
- Edge rate limiting (nginx/Cloudflare/ALB) in front of Duar -- required for volumetric/bad-auth defense (the app-layer aggregate does not cover decorated routes)

---

## Security Signals

Detect-only anomaly flags (Tier 1 of the [AI security roadmap](../ai-security-roadmap.md)) evaluated on login success, refresh-context change, and login failure. Signals become activity rows + `auth.signal.*` stream events surfaced on the admin Dashboard/Activity pages. They never block or alter an auth decision.

| Variable | Default | Description |
|----------|---------|-------------|
| `SIGNALS_ENABLED` | `true` | Master switch for all signal rules. |
| `SIGNAL_IMPOSSIBLE_TRAVEL_KMH` | `900` | Implied travel speed (country-centroid haversine) above which `login_impossible_travel` fires. |
| `SIGNAL_STUFFING_WINDOW_MINUTES` | `15` | Sliding window for per-IP login-failure counters. |
| `SIGNAL_STUFFING_FAILURES` | `10` | Failures per IP within the window required for `credential_stuffing_suspected` (AND the distinct-emails threshold). |
| `SIGNAL_STUFFING_DISTINCT_EMAILS` | `5` | Distinct emails per IP within the window required for `credential_stuffing_suspected`. |

---

## Admin

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_EMAILS` | `""` | Comma-separated emails granted admin access. Must be set before first admin login. |
| `ADMIN_URL` | `http://localhost:9004` | URL where the admin UI is served. Used for CORS and host validation. |

Add emails before starting the service. You can also promote existing users with `make create-admin`.

---

## Notes

**Service API keys** are managed through the admin panel (Service Apps), not environment variables. Each key is scoped to a `service_name` and stored as a SHA-256 hash.

**Comma-separated values** -- `CORS_ORIGINS`, `ALLOWED_HOSTS`, and `ADMIN_EMAILS` all accept multiple values separated by commas:

```dotenv
CORS_ORIGINS=https://app.example.com,https://admin.example.com
ALLOWED_HOSTS=api.example.com,admin.example.com
ADMIN_EMAILS=alice@example.com,bob@example.com
```
