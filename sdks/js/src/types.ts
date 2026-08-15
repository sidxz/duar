// ── Config ──────────────────────────────────────────────────────────

export interface DuarConfig {
  /** Base URL of the Duar identity service (e.g. "http://localhost:9003") */
  duarUrl: string
  /**
   * The Duar ClientApp ID (UUID) this frontend is registered as. Required —
   * the login flow binds this ID to the chosen redirect URI so a crafted link
   * cannot redirect a victim's login to a different app's callback. Obtain from
   * the Duar admin panel.
   */
  clientId: string
  /** OAuth redirect URI. Defaults to `${window.location.origin}/auth/callback` */
  redirectUri?: string
  /** Token storage backend. Defaults to in-memory (MemoryStore). Pass `new LocalStorageStore()` to persist across page reloads. */
  storage?: TokenStore
  /** Automatically refresh tokens before expiry. Defaults to true. */
  autoRefresh?: boolean
  /** Seconds before token expiry to trigger refresh. Defaults to 60. */
  refreshBuffer?: number
}

// ── Token storage ───────────────────────────────────────────────────

export interface TokenStore {
  getAccessToken(): string | null
  getRefreshToken(): string | null
  setTokens(access: string, refresh: string): void
  clear(): void
}

// ── Auth flow ───────────────────────────────────────────────────────

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface WorkspaceOption {
  id: string
  name: string
  slug: string
  role: string
}

// ── User ────────────────────────────────────────────────────────────

export type WorkspaceRole = 'owner' | 'admin' | 'editor' | 'viewer'

export interface DuarUser {
  userId: string
  email: string
  name: string
  workspaceId: string
  workspaceSlug: string
  workspaceRole: WorkspaceRole
  groups: string[]
}

// ── JWT payload ─────────────────────────────────────────────────────

export interface JWTPayload {
  sub: string
  email: string
  name: string
  wid: string
  wslug: string
  wrole: WorkspaceRole
  groups: string[]
  aud: string | string[]
  iss: string
  exp: number
  iat: number
  jti: string
}

// ── AuthZ JWT payload (authorization-only, no identity claims) ──────

export interface AuthzJWTPayload {
  sub: string
  idp_sub: string
  svc: string
  wid: string
  wslug: string
  wrole: WorkspaceRole
  actions: string[]
  aud: string | string[]
  iss: string
  exp: number
  iat: number
  jti: string
  type: 'authz'
}

// ── Permissions ─────────────────────────────────────────────────────

export interface PermissionCheck {
  service_name: string
  resource_type: string
  resource_id: string
  action: string
}

export interface PermissionResult extends PermissionCheck {
  allowed: boolean
}

export interface RegisterResourceRequest {
  service_name: string
  resource_type: string
  resource_id: string
  workspace_id: string
  owner_id: string
  visibility?: string
}

export interface ShareRequest {
  grantee_type: string
  grantee_id: string
  permission: string
}

export interface AccessibleResult {
  resource_ids: string[]
  has_full_access: boolean
}

// ── Roles ───────────────────────────────────────────────────────────

export interface ActionDefinition {
  action: string
  description?: string
}

// ── Server JWT verification ─────────────────────────────────────────

export interface VerifyOptions {
  /** URL to the JWKS endpoint (e.g. "http://localhost:9003/.well-known/jwks.json") */
  jwksUrl: string
  /** Expected audience claim. Defaults to "sentinel:access". */
  audience?: string
  /** Expected issuer claim. */
  issuer?: string
}

// ── Realm m2m (no-user) ─────────────────────────────────────────────

export interface M2mJWTPayload {
  svc: string
  caller: string
  actions: string[]
  aud_target: string | null
  aud: string | string[]
  iss: string
  exp: number
  iat: number
  jti: string
  type: 'm2m'
}

export interface WhoamiResponse {
  service_name: string
  effective_scope: string
  realm: { slug: string; name: string } | null
}

export interface M2mVerifyOptions {
  /** JWKS URL of the Duar that signs m2m tokens. */
  jwksUrl: string
  /** This service's shared scope (realm slug). The token's `svc` must equal it. */
  effectiveScope: string
  /** This service's own name — checked against `aud_target` when the token sets it. */
  serviceName?: string
  /** Expected issuer claim. */
  issuer?: string
}

/** No-user in-realm caller context — the counterpart to a DuarUser. */
export interface SystemAuth {
  caller: string
  actions: string[]
  svc: string
  can(action: string): boolean
}
