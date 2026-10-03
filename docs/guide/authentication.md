# Authentication

## Supported IdPs

Duar proxies authentication from three identity providers. Provider registration is conditional -- if the environment variables are not set, the provider is not available.

| Provider | Protocol | PKCE | Scopes |
|---|---|---|---|
| Google | OIDC | S256 | `openid email profile` |
| GitHub | OAuth2 | None | `user:email` |
| Microsoft Entra ID | OIDC | S256 | `openid email profile` |

**Google** -- Standard OIDC with automatic discovery. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

**GitHub** -- OAuth2 only (not OIDC, no PKCE). User info fetched via GitHub API. If the primary email is not in the profile response, it is fetched from `GET /user/emails`. Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`.

**Entra ID** -- OIDC with tenant-specific discovery. Set `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`, and `ENTRA_TENANT_ID`. The provider identifier is `entra_id`.

Entra's claims differ from the OIDC baseline in two ways that affect sign-in:

- **No `email_verified` claim.** Entra never emits it; its analogue is the optional `xms_edov` ("email domain owner verified") claim. Duar pins the issuer to the single tenant in `ENTRA_TENANT_ID`, so a token bearing that `tid` is treated as carrying a tenant-verified address — unless `xms_edov` is explicitly `false`, which is a hard reject. Tokens from any other tenant, and from any other IdP, get no such treatment and must carry `email_verified: true`; the exemption is bound to the provider the signature was verified against, not to the presence of a `tid` claim.

    !!! warning "Enable the `xms_edov` optional claim"
        Without it, Duar is trusting your tenant directory rather than an explicit
        per-address assertion. That is the normal trust model for a single-tenant
        deployment, but it means anyone who can influence the directory attribute
        behind the `email` claim can influence which address Duar sees — and the
        email drives organization resolution and `ADMIN_EMAILS` auto-promotion. Adding
        `xms_edov` to the app registration's token configuration turns that into a
        verified assertion Duar enforces.
- **`email` is often absent.** Managed work accounts only receive it if the app registration adds `email` as an optional ID-token claim; `*.onmicrosoft.com` dev-tenant accounts typically have no mail attribute at all. Duar then falls back to `preferred_username` (the UPN) when it is address-shaped. Identity is always keyed on `sub`, never on either address.

Since the email domain drives organization resolution, register the org domain that matches the addresses your tenant actually issues (e.g. `tptdevelorg.onmicrosoft.com` for a dev tenant), or sign-in is refused as "not permitted for this email domain".

For AuthZ mode, the browser goes to Entra directly with `response_type=id_token`, so the redirect URI must be registered under the **Web** platform with *ID tokens (used for implicit and hybrid flows)* enabled — a redirect URI registered under the *Single-page application* platform rejects implicit. Alternatively, acquire the ID token however you like (e.g. MSAL with auth code + PKCE) and hand it to `POST /authz/resolve` yourself.

See [How Duar Works](how-it-works.md) for the full login flows in both AuthZ and Proxy modes.

## Token Types

| Token | Audience | TTL | Purpose |
|---|---|---|---|
| Access | `duar:access` | 15 min | Identity + authorization in Proxy mode. Carries user info, workspace context, and group memberships. |
| Refresh | `duar:refresh` | 7 days | Silent renewal of access tokens. Supports rotation with reuse detection. |
| Admin | `duar:admin` | 60 min | Admin panel sessions. Carries `admin: true` flag. |
| Authz | `duar:authz` | 5 min | Authorization-only in AuthZ mode. Carries workspace role and RBAC actions. No identity -- identity comes from the IdP token. |

All tokens are RS256-signed JWTs. The algorithm is hardcoded at both encode and decode time to prevent algorithm substitution attacks. Audience validation is mandatory on every decode call.

## JWT Claims

### Access Token Claims

| Claim | Type | Example | Description |
|---|---|---|---|
| `iss` | string | `https://auth.example.com` | Issuer. Duar's `BASE_URL`. |
| `sub` | string (UUID) | `"d4f5a..."` | User ID. |
| `jti` | string (UUID) | `"8b3c1..."` | Unique token ID. Enables per-token revocation via Redis denylist. |
| `aud` | string | `"duar:access"` | Audience. Always `duar:access`. |
| `email` | string | `"alice@co.com"` | User's email address. |
| `name` | string | `"Alice Chen"` | User's display name. |
| `wid` | string (UUID) | `"a1b2c..."` | Workspace ID. |
| `wslug` | string | `"acme"` | Workspace slug. |
| `wrole` | string | `"editor"` | Workspace role: `owner`, `admin`, `editor`, or `viewer`. |
| `groups` | string[] | `["uuid1", "uuid2"]` | Group IDs the user belongs to in this workspace. |
| `iat` | number | `1709827200` | Issued-at timestamp (UTC). |
| `exp` | number | `1709828100` | Expiration timestamp (UTC). `iat` + 15 minutes. |
| `type` | string | `"access"` | Token type discriminator. |

### Authz Token Claims

| Claim | Type | Example | Description |
|---|---|---|---|
| `iss` | string | `https://auth.example.com` | Issuer. Duar's `BASE_URL`. |
| `sub` | string (UUID) | `"d4f5a..."` | User ID. |
| `jti` | string (UUID) | `"8b3c1..."` | Unique token ID. Enables revocation via denylist. |
| `aud` | string | `"duar:authz"` | Audience. Always `duar:authz`. |
| `idp_sub` | string | `"104523..."` | IdP subject identifier. Binds this token to a specific IdP identity. The backend validates that the IdP token's `sub` matches this value. |
| `svc` | string | `"docu-store"` | Service name. Binds the token to a specific service, preventing cross-service replay. |
| `wid` | string (UUID) | `"a1b2c..."` | Workspace ID. |
| `wslug` | string | `"acme"` | Workspace slug. |
| `wrole` | string | `"editor"` | Workspace role. |
| `actions` | string[] | `["docs:read", "docs:write"]` | RBAC actions granted to this user for this service in this workspace. |
| `iat` | number | `1709827200` | Issued-at timestamp (UTC). |
| `exp` | number | `1709827500` | Expiration timestamp (UTC). `iat` + 5 minutes. |
| `type` | string | `"authz"` | Token type discriminator. |

### Refresh Token Claims

| Claim | Type | Example | Description |
|---|---|---|---|
| `iss` | string | `https://auth.example.com` | Issuer. |
| `sub` | string (UUID) | `"d4f5a..."` | User ID. |
| `jti` | string (UUID) | `"8b3c1..."` | Unique token ID. |
| `aud` | string | `"duar:refresh"` | Audience. Always `duar:refresh`. |
| `fid` | string (UUID) | `"f7e8d..."` | Family ID. Groups refresh tokens into rotation families for reuse detection. |
| `iat` | number | `1709827200` | Issued-at timestamp (UTC). |
| `exp` | number | `1710432000` | Expiration timestamp (UTC). `iat` + 7 days. |
| `type` | string | `"refresh"` | Token type discriminator. |

### Admin Token Claims

| Claim | Type | Example | Description |
|---|---|---|---|
| `iss` | string | `https://auth.example.com` | Issuer. |
| `sub` | string (UUID) | `"d4f5a..."` | User ID. |
| `jti` | string (UUID) | `"8b3c1..."` | Unique token ID. |
| `aud` | string | `"duar:admin"` | Audience. Always `duar:admin`. |
| `email` | string | `"alice@co.com"` | Admin user's email. |
| `name` | string | `"Alice Chen"` | Admin user's display name. |
| `admin` | boolean | `true` | Always `true`. |
| `iat` | number | `1709827200` | Issued-at timestamp (UTC). |
| `exp` | number | `1709830800` | Expiration timestamp (UTC). `iat` + 60 minutes. |
| `type` | string | `"admin_access"` | Token type discriminator. |

## Token Lifecycle

### Refresh Rotation with Reuse Detection

Refresh tokens use a family-based rotation scheme:

1. On login, a refresh token is issued with a unique `fid` (family ID).
2. When the client refreshes, the old refresh token is consumed and a new one is issued with the same `fid`.
3. If a consumed refresh token is presented again (reuse), the entire family is invalidated -- all tokens sharing that `fid` are denied.

This detects token theft: if an attacker steals a refresh token and uses it, either the attacker or the legitimate user will trigger reuse detection, invalidating the family.

### Revocation

Individual tokens are revoked by adding their `jti` to a Redis denylist. The denylist entry TTL matches the token's remaining lifetime, so entries self-clean. Every token validation checks the denylist before accepting the token.

Logout revokes both the access token and the refresh token's entire family.
