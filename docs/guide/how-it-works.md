# How Duar Works

## What Duar Does

Duar is an authentication proxy and authorization microservice. It does not store passwords or act as an identity provider -- users always authenticate through external IdPs (Google, GitHub, EntraID). Duar validates those IdP credentials, provisions users, and layers on workspace roles, RBAC, and per-resource permissions.

## AuthZ Mode (Recommended)

Your app authenticates users directly with the IdP using its native SDK. Duar handles authorization only.

```mermaid
sequenceDiagram
    participant Browser
    participant IdP
    participant App as Your Backend
    participant Duar

    Browser->>IdP: Sign in (Google SDK, MSAL, etc.)
    IdP-->>Browser: IdP token (1hr)

    Note over Browser,Duar: Step 1 — discover workspaces (browser-direct, no credential issued)
    Browser->>Duar: POST /authz/resolve<br/>{idp_token, provider}
    Duar->>IdP: Validate token against JWKS
    Duar-->>Browser: {user, workspaces}

    Note over Browser,Duar: Step 2 — mint authz JWT (goes through backend with service key)
    Browser->>App: POST /api/auth/mint<br/>{idp_token, provider, workspace_id, nonce}
    App->>Duar: POST /authz/resolve<br/>+ X-Service-Key: sk_...
    Duar->>IdP: Re-validate, check nonce
    Duar->>Duar: JIT provision user,<br/>resolve workspace role + RBAC actions
    Duar-->>App: {authz_token (5min), user, workspace}
    App-->>Browser: {authz_token, user, workspace}

    Note over Browser,App: Subsequent API requests send both tokens
    Browser->>App: API request<br/>Authorization: Bearer {idp_token}<br/>X-Authz-Token: {authz_token}
    App->>App: AuthzMiddleware validates both tokens,<br/>checks idp_sub + svc bindings
    App-->>Browser: Response
```

The client sends two tokens on every API request:

- **IdP token** (`Authorization` header) -- proves identity. Issued by Google/GitHub/EntraID, typically valid for 1 hour.
- **Authz token** (`X-Authz-Token` header) -- carries authorization context. Issued by Duar, valid for 5 minutes. Contains workspace role, RBAC actions, and an `idp_sub` claim that binds it to the IdP identity.

**Minting vs. discovery.** The browser calls Duar directly to *discover* workspaces (no credential issued). Minting the authz JWT goes through your backend (`POST /api/auth/mint` in the diagram) because credential issuance requires a service key — the browser doesn't hold one. See [AuthZ Mode Security](../security.md#authz-mode-security).

The backend's `AuthzMiddleware` validates both tokens independently and verifies the `idp_sub` binding -- the IdP token's `sub` must match the authz token's `idp_sub`. This prevents an attacker from pairing a stolen authz token with a different identity.

The authz token also carries an `svc` claim binding it to the requesting service, preventing cross-service replay. The middleware enforces this alongside the IdP token's `aud` (OAuth client_id) — all checked on every request.

## Proxy Mode

Duar handles the entire OAuth2/OIDC flow. The client gets a single JWT.

```mermaid
sequenceDiagram
    participant Browser
    participant App as Your Frontend
    participant Duar
    participant IdP

    Browser->>App: Click "Sign in with Google"
    App->>App: Generate PKCE code_verifier + code_challenge
    App->>Duar: GET /auth/login/{provider}<br/>?redirect_uri=Y&code_challenge=Z

    Duar->>IdP: Redirect to authorization URL
    IdP->>Browser: Consent screen
    Browser->>IdP: Grant consent
    IdP->>Duar: Authorization code callback

    Duar->>IdP: Exchange code for tokens
    Duar->>Duar: Extract user info, JIT provision
    Duar-->>App: Redirect with ?code=X

    App->>Duar: POST /auth/token<br/>{code, workspace_id, code_verifier}
    Duar-->>App: {access_token (15min), refresh_token (7d)}
```

Duar acts as an OAuth2 client, managing the redirect flow, PKCE, and token exchange. Your app receives a single access token containing the user's identity and workspace context. Refresh tokens support silent renewal.

## When to Use Which

| | AuthZ Mode | Proxy Mode |
|---|---|---|
| **IdP login** | Your app handles it | Duar handles it |
| **Tokens per request** | 2 (IdP + authz) | 1 (access) |
| **Works with** | Firebase Auth, Supabase Auth, Auth0, any OIDC provider | Google, GitHub, EntraID (configured in Duar) |
| **Authz token TTL** | 5 minutes | N/A (15 min access token) |
| **Flexibility** | High -- use any IdP SDK, any login UI | Lower -- Duar controls the flow |
| **Best for** | Apps that already have IdP integration | New apps wanting turnkey auth |

## Three-Tier Authorization

Both modes feed into the same authorization system:

1. **Workspace Roles** (JWT claims) -- coarse-grained: `owner`, `admin`, `editor`, `viewer`. Embedded in the token, checked without DB calls. See [Workspaces](workspaces.md).

2. **Custom Roles / RBAC** (DB) -- action-based: "can this user do `reports:export`?" Roles bundle actions; users are assigned roles per workspace. See [Roles](roles.md).

3. **Entity ACLs** (Zanzibar-style, DB) -- per-resource: "can this user edit document X?" Generic `(service_name, resource_type, resource_id)` tuples. See [Permissions](permissions.md).

Each tier is additive. Workspace roles provide the baseline. RBAC adds fine-grained action checks. Entity ACLs add per-object access control.
