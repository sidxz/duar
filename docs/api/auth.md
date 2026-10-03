# Authentication Endpoints

Duar supports two authentication modes: **AuthZ Mode** (token validation) and **Proxy Mode** (full OAuth flow).

---

## AuthZ Mode

Your backend validates an IdP token directly with Duar and receives an authorization JWT. No browser redirects.

### POST /authz/resolve

Validate an IdP token, provision the user (JIT), and return authorization context.

**Auth:** `X-Service-Key` header or matching `Origin` header.

**Request Body:**

```json
{
  "idp_token": "eyJhbGciOi...",
  "provider": "google",
  "workspace_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `idp_token` | string | Yes | Raw IdP token (OIDC ID token or OAuth access token) |
| `provider` | string | Yes | Provider name: `google`, `github`, `entra_id` |
| `workspace_id` | UUID | No | Workspace to authorize for. Omit to get workspace list. |
| `nonce` | string | No | Replay-protection nonce. If provided, Duar requires the IdP token's `nonce` claim to match (OIDC providers only — ignored for GitHub opaque tokens). Browsers should pass the same nonce they set at login-start. |

**Response (with workspace_id):** `200 OK`

```json
{
  "user": {"id": "...", "email": "j@example.com", "name": "Jane"},
  "workspace": {"id": "...", "slug": "acme", "role": "admin"},
  "authz_token": "eyJhbGciOi...",
  "expires_in": 900
}
```

**Response (without workspace_id):** `200 OK`

```json
{
  "user": {"id": "...", "email": "j@example.com", "name": "Jane"},
  "workspaces": [
    {"id": "...", "name": "Acme Corp", "slug": "acme", "role": "admin"}
  ]
}
```

**Errors:** `400` invalid IdP token, provider, or nonce mismatch; `403` inactive user or not a workspace member; `409` an account with this email exists under a different IdP — sign in with the original provider.

```bash
curl -X POST http://localhost:9003/authz/resolve \
  -H "X-Service-Key: sk_your_key" \
  -H "Content-Type: application/json" \
  -d '{"idp_token": "eyJ...", "provider": "google", "workspace_id": "550e8400-..."}'
```

**Rate limit:** 10/min.

### GET /authz/idp/github/login

Server-side OAuth proxy for GitHub (GitHub does not support implicit flow, so the browser cannot obtain an ID token directly). Call this from the browser to start a GitHub AuthZ-mode login.

| Parameter | In | Required | Description |
|---|---|---|---|
| `provider` | path | Yes | Only `github` is supported |
| `redirect_uri` | query | Yes | Where to send the token after login. **Must match an origin registered on an active `ServiceApp.allowed_origins`.** Unregistered origins are rejected with `400` — this prevents an attacker from harvesting a victim's GitHub access token by pointing the flow at their own site. |
| `nonce` | query | Yes | Replay-protection nonce echoed back to the callback |

**Response:** `302` redirect to GitHub.

### GET /authz/idp/github/callback

Handles the GitHub OAuth callback. Duar exchanges the code for a GitHub access token, then redirects to `{redirect_uri}#id_token={token}&nonce={nonce}` (token in URL fragment). The `redirect_uri` is re-validated against `ServiceApp.allowed_origins` — an admin who removes an origin between login-start and callback will see this 400 out, not succeed.

**Rate limit:** 10/min on both endpoints.

---

## Proxy Mode

Full OAuth2 + PKCE flow with browser redirects. The SDK handles this automatically.

### Endpoint Table

| Method | Path | Auth | Rate Limit |
|---|---|---|---|
| GET | `/auth/providers` | None | -- |
| GET | `/auth/login/{provider}` | None | 10/min |
| GET | `/auth/callback/{provider}` | None | 10/min |
| POST | `/auth/workspaces` | None | 10/min |
| POST | `/auth/token` | None | 10/min |
| POST | `/auth/refresh` | None | 10/min |
| POST | `/auth/logout` | Bearer JWT | -- |

### GET /auth/providers

Returns configured OAuth providers.

**Response:** `200 OK`

```json
{"providers": ["google", "github"]}
```

### GET /auth/login/{provider}

Starts the OAuth flow. Redirects to the provider's consent screen.

| Parameter | In | Required | Description |
|---|---|---|---|
| `provider` | path | Yes | Provider name (`google`, `github`, `entra_id`) |
| `client_id` | query | Yes | ClientApp UUID. Duar validates `redirect_uri` against THIS client app only — not any active app. Prevents cross-app auth-code interception. |
| `redirect_uri` | query | Yes | Must be listed on the client app's registered `redirect_uris` |
| `code_challenge` | query | Yes | PKCE S256 challenge |
| `code_challenge_method` | query | No | Only `S256` supported (default) |
| `state` | query | No | Opaque SPA CSRF state (max 512 chars) — echoed back verbatim on the final callback redirect |

**Response:** `302` redirect to provider. On the callback the same `client_app_id` is re-validated against `redirect_uri` before the auth code is issued.

### GET /auth/callback/{provider}

Handles the OAuth callback. Creates/updates the user, generates a single-use authorization code, and redirects to `{redirect_uri}?code={code}` (plus `state={state}` echoed verbatim when the SPA passed `state` to `/auth/login/{provider}`).

The authorization code expires in 5 minutes and is stored in Redis.

### POST /auth/workspaces

Lists workspaces for a user identified by authorization code. Used for workspace selection. Requires the PKCE verifier: the code travels in the redirect URL (history/logs/Referer), so possession of a leaked code alone must not disclose workspace names/slugs/roles. The code is not consumed — `POST /auth/token` redeems it afterwards.

**Request Body:**

```json
{
  "code": "authorization_code",
  "code_verifier": "original_code_verifier_string_43_to_128_chars"
}
```

**Response:** `200 OK`

```json
[
  {"id": "...", "name": "Acme Corp", "slug": "acme", "role": "admin"}
]
```

### POST /auth/token

Exchanges authorization code + workspace selection + PKCE verifier for JWT tokens.

**Request Body:**

```json
{
  "code": "authorization_code",
  "workspace_id": "550e8400-e29b-41d4-a716-446655440000",
  "code_verifier": "original_code_verifier_string_43_to_128_chars"
}
```

**Response:** `200 OK`

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "dGhpcyBpcy...",
  "token_type": "bearer",
  "expires_in": 900
}
```

**Errors:** `400` invalid/expired code or PKCE failure, `403` not a workspace member, `404` user/workspace not found.

```bash
curl -X POST http://localhost:9003/auth/token \
  -H "Content-Type: application/json" \
  -d '{"code": "abc123", "workspace_id": "550e8400-...", "code_verifier": "your_verifier"}'
```

### POST /auth/refresh

Rotates a refresh token. The old token is invalidated. Reuse of an already-rotated token revokes the entire family.

**Request Body:**

```json
{"refresh_token": "dGhpcyBpcy..."}
```

**Response:** `200 OK` -- same shape as `/auth/token`.

**Errors:** `401` invalid, expired, or reused refresh token.

```bash
curl -X POST http://localhost:9003/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "your_refresh_token"}'
```

### POST /auth/logout

Revokes all refresh token families for the user and blacklists the current access token's `jti` in Redis.

**Auth:** Bearer JWT required.

**Response:** `200 OK`

```json
{"ok": true}
```

```bash
curl -X POST http://localhost:9003/auth/logout \
  -H "Authorization: Bearer eyJhbGciOi..."
```
