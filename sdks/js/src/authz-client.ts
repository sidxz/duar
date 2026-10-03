import { AuthzMemoryStore } from './authz-storage'
import { authzTokenToUser, isTokenExpired, parseJwt } from './jwt-utils'
import { warnIfInsecure } from './warn-insecure'
import type {
  DuarAuthzConfig,
  AuthzTokenStore,
  AuthzResolveResponse,
  AuthState,
  AuthzCallbackResult,
  IdpConfig,
  WorkspaceMember,
  GroupInfo,
  GroupMemberInfo,
  UserProfile,
} from './authz-types'
import type { DuarUser } from './types'

type AuthStateListener = (user: DuarUser | null) => void

// How long a silent (`prompt=none`) re-auth is considered "in flight". The marker
// stores the attempt's start time; once it is older than this, the loop guard no
// longer blocks a fresh attempt. This bounds recovery: a silent attempt that never
// completes (Back button, stalled redirect, an errored callback that didn't clear
// the marker, a multi-workspace picker the user abandoned) can no longer wedge the
// session — silentLogin can retry after the window instead of no-op'ing forever.
const SILENT_TTL_MS = 60_000

/**
 * Browser auth client for Duar AuthZ mode.
 * Manages dual tokens: IdP token (identity) + Duar authz token (authorization).
 */
export class DuarAuthz {
  private readonly duarUrl: string
  private readonly store: AuthzTokenStore
  private readonly autoRefresh: boolean
  private readonly refreshBuffer: number
  private readonly idps: Record<string, IdpConfig>
  private readonly redirectUri: string
  private refreshTimer: ReturnType<typeof setTimeout> | null = null
  private readonly mintEndpoint: string
  private refreshPromise: Promise<boolean> | null = null
  private listeners: Set<AuthStateListener> = new Set()

  constructor(config: DuarAuthzConfig) {
    this.duarUrl = config.duarUrl.replace(/\/+$/, '')
    if (!config.mintEndpoint) {
      throw new Error(
        'DuarAuthz: mintEndpoint is required. Expose a backend route that ' +
        'calls Duar\'s /authz/resolve with your service key (e.g. "/api/auth/mint"). ' +
        'The browser must not mint authz tokens directly — it lacks a service key.',
      )
    }
    this.mintEndpoint = config.mintEndpoint
    this.store = config.storage ?? new AuthzMemoryStore()
    this.autoRefresh = config.autoRefresh ?? true
    this.refreshBuffer = config.refreshBuffer ?? 30
    this.idps = config.idps ?? {}
    this.redirectUri = config.redirectUri
      ?? (typeof window !== 'undefined' ? `${window.location.origin}/auth/callback` : '')
    warnIfInsecure(this.duarUrl, 'DuarAuthz')

    if (this.autoRefresh && this.store.getAuthzToken()) {
      this.scheduleRefresh()
    }
  }

  // ── Login ───────────────────────────────────────────────────────────

  /** Redirect to IdP login page. Requires the provider to be configured in `idps`. */
  login(provider: string): void {
    const idp = this.idps[provider]
    if (!idp) {
      throw new Error(
        `IdP "${provider}" not configured. Pass it via idps in DuarAuthzConfig, ` +
        `e.g. { idps: { google: IdpConfigs.google('your-client-id') } }`
      )
    }

    const nonce = crypto.randomUUID()
    sessionStorage.setItem('duar_authz_nonce', nonce)
    sessionStorage.setItem('duar_authz_provider', provider)
    // Interactive login supersedes any in-flight silent attempt so the callback
    // is not misclassified as silent (which would suppress real errors).
    sessionStorage.removeItem('duar_authz_silent')

    const params = new URLSearchParams({
      client_id: idp.clientId,
      redirect_uri: this.redirectUri,
      response_type: idp.responseType ?? 'id_token',
      scope: (idp.scopes ?? ['openid', 'email', 'profile']).join(' '),
      nonce,
      ...idp.extraParams,
    })

    window.location.href = `${idp.authorizationUrl}?${params}`
  }

  /**
   * Handle the OAuth callback. Extracts the id_token from the URL hash and
   * returns a discriminated result.
   *
   * Call this from your callback route. Returns:
   * - ``{ status: 'success', idpToken, provider, returnTo }`` — proceed to
   *   ``resolve`` / ``selectWorkspace``, then navigate to ``returnTo ?? '/'``.
   * - ``{ status: 'silent_failed', ... }`` — a ``silentLogin`` ``prompt=none``
   *   attempt could not complete without user interaction. NOT fatal: fall back
   *   to interactive ``login(provider)``.
   * - ``null`` — no IdP response in the URL (not a callback).
   *
   * Throws on a genuine OAuth error or a nonce mismatch (possible replay).
   *
   * @param capturedHash Optional pre-captured URL fragment (without the leading
   *   ``#``). React components capture the hash at module load to survive
   *   StrictMode double-mount; pass it here so the same value is interpreted.
   */
  handleCallback(capturedHash?: string): AuthzCallbackResult | null {
    const hash = capturedHash ?? window.location.hash.substring(1)
    if (!hash) return null

    const params = new URLSearchParams(hash)
    const idpToken = params.get('id_token')
    const error = params.get('error')
    const silent = this.isSilentAttempt()
    const storedProvider =
      (typeof sessionStorage !== 'undefined' && sessionStorage.getItem('duar_authz_provider')) || null

    if (error) {
      // Clean the URL, consume markers regardless of outcome.
      window.history.replaceState({}, '', window.location.pathname)
      const returnTo = this.consumeReturnTo()
      this.clearCallbackMarkers()
      // Any IdP error during a silent (prompt=none) attempt is recoverable:
      // signal silent_failed so the caller falls back to interactive login
      // (which surfaces a genuine error there) rather than throwing here.
      // Throwing under autoReauth would turn a persistent error — login_required,
      // but also account_selection_required, access_denied, etc. — into a
      // reload-driven loop.
      if (silent) {
        return { status: 'silent_failed', error, provider: storedProvider, returnTo }
      }
      throw new Error(params.get('error_description') || error)
    }

    if (!idpToken) return null

    // Verify nonce to prevent token replay / login-CSRF injection.
    // Nonce may come from URL hash params (proxy flow, e.g. GitHub) or JWT claims
    // (implicit flow, e.g. Google). If no nonce was stored at login start, the
    // callback did NOT originate from a flow we initiated — fail closed so an
    // attacker cannot inject an id_token by pointing the victim at the callback
    // URL directly.
    const expectedNonce = sessionStorage.getItem('duar_authz_nonce')
    if (!expectedNonce) {
      throw new Error(
        'No login flow in progress — callback rejected. Start login from this tab.',
      )
    }
    const hashNonce = params.get('nonce')
    if (hashNonce) {
      if (hashNonce !== expectedNonce) {
        throw new Error('Nonce mismatch — possible token replay')
      }
    } else {
      const claims = parseJwt(idpToken) as unknown as Record<string, unknown>
      if (claims.nonce !== expectedNonce) {
        throw new Error('Nonce mismatch — possible token replay')
      }
    }

    // Clean the URL
    window.history.replaceState({}, '', window.location.pathname)

    const returnTo = this.consumeReturnTo()
    // Consume the replay nonce + provider now, but DEFER clearing the silent
    // marker to selectWorkspace(): until the IdP token is actually set the
    // session is still ``needs_reauth``, so a parent provider's autoReauth must
    // keep no-op'ing (React fires child effects before parent effects, so the
    // callback runs first — clearing the marker here would let the provider
    // fire a second redirect mid-resolve).
    sessionStorage.removeItem('duar_authz_nonce')
    sessionStorage.removeItem('duar_authz_provider')

    return { status: 'success', idpToken, provider: storedProvider ?? 'google', returnTo }
  }

  /**
   * Begin a silent re-authentication via a top-level ``prompt=none`` redirect
   * to the IdP. Use this when {@link getAuthState} is ``needs_reauth`` (e.g.
   * after a page reload, where the memory-only IdP token is gone).
   *
   * With a live IdP session and prior consent the IdP bounces straight back to
   * the callback with a fresh ``id_token`` (usually no UI). On the callback,
   * {@link handleCallback} returns ``status: 'silent_failed'`` if the IdP needs
   * interaction — fall back to {@link login} then.
   *
   * A full-page redirect is used deliberately rather than a hidden iframe:
   * third-party-cookie restrictions make iframe ``prompt=none`` unreliable.
   *
   * @returns ``true`` if a redirect was started, ``false`` if it was a no-op
   *   (no provider known, or a silent attempt is already in flight).
   * @throws if the resolved provider is not configured in ``idps``.
   */
  silentLogin(provider?: string): boolean {
    if (typeof window === 'undefined' || typeof sessionStorage === 'undefined') return false
    // Loop guard: never start a second silent attempt while one is genuinely in
    // flight. A stale marker (older than SILENT_TTL_MS) no longer blocks, so an
    // abandoned/errored attempt can't wedge the session permanently.
    if (this.silentInFlight()) return false

    const prov = provider ?? this.store.getProvider()
    if (!prov) return false // no IdP to silently re-auth against

    const idp = this.idps[prov]
    if (!idp) {
      throw new Error(
        `IdP "${prov}" not configured. Pass it via idps in DuarAuthzConfig, ` +
        `e.g. { idps: { google: IdpConfigs.google('your-client-id') } }`,
      )
    }

    const nonce = crypto.randomUUID()
    sessionStorage.setItem('duar_authz_nonce', nonce)
    sessionStorage.setItem('duar_authz_provider', prov)
    // Store the attempt start time (not a bare flag) so the loop guard can expire it.
    sessionStorage.setItem('duar_authz_silent', String(Date.now()))
    sessionStorage.setItem('duar_authz_return_to', window.location.pathname + window.location.search)

    // Spread extraParams FIRST so the silent-flow essentials below (notably
    // prompt='none') always win — a configured extraParams.prompt (e.g. Google's
    // 'select_account') must not silently turn this into an interactive redirect.
    const params = new URLSearchParams({
      ...idp.extraParams,
      client_id: idp.clientId,
      redirect_uri: this.redirectUri,
      response_type: idp.responseType ?? 'id_token',
      scope: (idp.scopes ?? ['openid', 'email', 'profile']).join(' '),
      nonce,
      prompt: 'none',
    })
    // login_hint targets the previously-signed-in account so the IdP can
    // resolve the session without an account picker. Email is non-secret.
    const email = this.store.getUserIdentity()?.email
    if (email) params.set('login_hint', email)

    window.location.href = `${idp.authorizationUrl}?${params}`
    return true
  }

  /**
   * Read and clear the stored post-reauth return path, validated to be a
   * same-origin absolute path. Returns null if absent or unsafe (open-redirect
   * guard): rejects protocol-relative (``//host``), scheme-bearing (``http:``),
   * and non-rooted values.
   */
  consumeReturnTo(): string | null {
    if (typeof sessionStorage === 'undefined') return null
    const raw = sessionStorage.getItem('duar_authz_return_to')
    sessionStorage.removeItem('duar_authz_return_to')
    if (!raw) return null
    if (!raw.startsWith('/') || raw.startsWith('//')) return null
    if (raw.includes('://') || raw.includes('\\')) return null
    return raw
  }

  /** True if THIS callback is the result of a silent attempt we started (marker
   *  present, regardless of age — the redirect round-trip may exceed the TTL). */
  private isSilentAttempt(): boolean {
    return typeof sessionStorage !== 'undefined' && sessionStorage.getItem('duar_authz_silent') != null
  }

  /** True only while a recent silent attempt is genuinely in flight (used by the
   *  loop guard). A stale marker is treated as not-in-flight so a new attempt can
   *  start, which is what stops an abandoned attempt from wedging the session. */
  private silentInFlight(): boolean {
    if (typeof sessionStorage === 'undefined') return false
    const raw = sessionStorage.getItem('duar_authz_silent')
    if (raw == null) return false
    const startedAt = Number(raw)
    if (!Number.isFinite(startedAt)) return false
    return Date.now() - startedAt < SILENT_TTL_MS
  }

  private clearCallbackMarkers(): void {
    if (typeof sessionStorage === 'undefined') return
    sessionStorage.removeItem('duar_authz_nonce')
    sessionStorage.removeItem('duar_authz_provider')
    sessionStorage.removeItem('duar_authz_silent')
    sessionStorage.removeItem('duar_authz_return_to')
  }

  // ── Auth flow ───────────────────────────────────────────────────────

  /** Resolve an IdP token to discover the user's available workspaces. */
  async resolve(idpToken: string, provider: string): Promise<AuthzResolveResponse> {
    const res = await fetch(`${this.duarUrl}/authz/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idp_token: idpToken, provider }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error((body as Record<string, string>).detail || `HTTP ${res.status}`)
    }
    return res.json()
  }

  /**
   * Select a workspace and exchange the IdP token for a Duar authz token.
   *
   * The POST goes to the configured ``mintEndpoint`` on YOUR backend, not to
   * Duar directly. Your backend must forward the request to Duar's
   * ``/authz/resolve`` with ``X-Service-Key`` set. See DuarAuthzConfig.
   */
  async selectWorkspace(idpToken: string, provider: string, workspaceId: string): Promise<void> {
    const body: Record<string, string> = {
      idp_token: idpToken,
      provider,
      workspace_id: workspaceId,
    }
    // Forward the login-flow nonce so the backend can pass it to Duar's
    // /authz/resolve, which enforces replay protection against the IdP token.
    const nonce = typeof sessionStorage !== 'undefined'
      ? sessionStorage.getItem('duar_authz_nonce')
      : null
    if (nonce) body.nonce = nonce

    const res = await fetch(this.mintEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const parsed = await res.json().catch(() => ({}))
      throw new Error((parsed as Record<string, string>).detail || 'Token exchange failed')
    }

    const data: AuthzResolveResponse = await res.json()
    if (!data.authz_token) {
      throw new Error('No authz token in response')
    }

    if (data.user) {
      this.store.setUserIdentity({ email: data.user.email, name: data.user.name })
    }
    this.store.setTokens(idpToken, data.authz_token, provider, workspaceId)
    // The IdP token is now set, so the session is fully authenticated — release
    // the silent-reauth loop guard (see handleCallback's deferred clear).
    if (typeof sessionStorage !== 'undefined') {
      sessionStorage.removeItem('duar_authz_silent')
    }
    this.notify()
    if (this.autoRefresh) this.scheduleRefresh()
  }

  // ── Token access ──────────────────────────────────────────────────

  /**
   * Current derived auth state.
   *
   * Crucially this consults BOTH tokens. A valid authz token alone is not
   * enough to authenticate a request — the memory-only IdP token is also
   * required and is gone after a reload. Reporting that honestly (as
   * ``needs_reauth``) is what prevents the "zombie session" where the app
   * renders logged-in but every request 401s with "Missing IdP token".
   */
  getAuthState(): AuthState {
    const authzToken = this.store.getAuthzToken()
    if (!authzToken || isTokenExpired(authzToken)) return 'unauthenticated'
    if (!this.store.getIdpToken()) return 'needs_reauth'
    return 'authenticated'
  }

  /** Parse the current authz token + cached identity into a DuarUser, or null. */
  getUser(): DuarUser | null {
    if (this.getAuthState() !== 'authenticated') return null
    try {
      return authzTokenToUser(this.store.getAuthzToken()!, this.store.getUserIdentity())
    } catch {
      return null
    }
  }

  /** True only when a request can actually be authenticated (authz + IdP token). */
  get isAuthenticated(): boolean {
    return this.getAuthState() === 'authenticated'
  }

  /** True when an authz token survives but the IdP token is gone (e.g. after reload). */
  get needsReauth(): boolean {
    return this.getAuthState() === 'needs_reauth'
  }

  /** Get auth headers for API requests (both IdP and authz tokens). */
  getHeaders(): Record<string, string> {
    const idpToken = this.store.getIdpToken()
    const authzToken = this.store.getAuthzToken()
    if (!idpToken || !authzToken) return {}
    return {
      Authorization: `Bearer ${idpToken}`,
      'X-Authz-Token': authzToken,
    }
  }

  // ── Fetch wrapper ─────────────────────────────────────────────────

  /** Fetch with automatic dual-token headers and 401→refresh→retry. */
  async fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const doFetch = () => {
      const headers = new Headers(init?.headers)
      const authHeaders = this.getHeaders()
      for (const [k, v] of Object.entries(authHeaders)) {
        headers.set(k, v)
      }
      return fetch(input, { ...init, headers })
    }

    let res = await doFetch()

    if (res.status === 401) {
      const refreshed = await this.refresh()
      if (refreshed) {
        res = await doFetch()
      }
    }

    return res
  }

  /** Fetch JSON with automatic dual-token headers, 401 retry, and response parsing. */
  async fetchJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
    const headers = new Headers(init?.headers)
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    const res = await this.fetch(input, { ...init, headers })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error((body as Record<string, string>).detail || `HTTP ${res.status}`)
    }
    return res.json()
  }

  // ── Refresh ───────────────────────────────────────────────────────

  /** Refresh the authz token using the stored IdP token. Returns true on success. */
  async refresh(): Promise<boolean> {
    if (this.refreshPromise) return this.refreshPromise
    this.refreshPromise = this._doRefresh().finally(() => {
      this.refreshPromise = null
    })
    return this.refreshPromise
  }

  private async _doRefresh(): Promise<boolean> {
    const idpToken = this.store.getIdpToken()
    const provider = this.store.getProvider()
    const workspaceId = this.store.getWorkspaceId()
    // When the IdP token is no longer available (e.g. after a page reload —
    // the default store keeps it in memory only), silent refresh is not
    // possible and the user must re-authenticate via the IdP. Clear stale
    // state and let the app redirect to login on next interaction.
    if (!idpToken || !provider || !workspaceId) {
      this.store.clear()
      this.clearRefreshTimer()
      this.notify()
      return false
    }

    try {
      await this.selectWorkspace(idpToken, provider, workspaceId)
      return true
    } catch {
      this.store.clear()
      this.clearRefreshTimer()
      this.notify()
      return false
    }
  }

  // ── Events ────────────────────────────────────────────────────────

  /** Subscribe to auth state changes. Returns an unsubscribe function. */
  onAuthStateChange(cb: AuthStateListener): () => void {
    this.listeners.add(cb)
    return () => { this.listeners.delete(cb) }
  }

  /** Clear tokens and notify listeners. */
  logout(): void {
    this.store.clear()
    this.clearRefreshTimer()
    // Also drop any in-flight silent-reauth markers so a later mount starts clean.
    if (typeof sessionStorage !== 'undefined') {
      sessionStorage.removeItem('duar_authz_silent')
      sessionStorage.removeItem('duar_authz_return_to')
    }
    this.notify()
  }

  /** Clean up timers. Call when done (e.g. component unmount). */
  destroy(): void {
    this.clearRefreshTimer()
    this.listeners.clear()
  }

  // ── Workspace & group helpers ──────────────────────────────────────

  /**
   * Search workspace members by name or email.
   * Calls Duar's GET /workspaces/{id}/members?q=&limit= with dual-token auth.
   */
  async searchMembers(query?: string, limit?: number): Promise<WorkspaceMember[]> {
    const user = this.getUser()
    if (!user) throw new Error('Not authenticated')
    const params = new URLSearchParams()
    if (query) params.set('q', query)
    if (limit !== undefined) params.set('limit', String(limit))
    const qs = params.toString()
    const url = `${this.duarUrl}/workspaces/${user.workspaceId}/members${qs ? `?${qs}` : ''}`
    return this.fetchJson<WorkspaceMember[]>(url)
  }

  /** List all members of the current workspace. */
  async listMembers(limit?: number): Promise<WorkspaceMember[]> {
    return this.searchMembers(undefined, limit)
  }

  /** List groups in the current workspace. */
  async listGroups(): Promise<GroupInfo[]> {
    const user = this.getUser()
    if (!user) throw new Error('Not authenticated')
    return this.fetchJson<GroupInfo[]>(`${this.duarUrl}/workspaces/${user.workspaceId}/groups`)
  }

  /** List members of a group. */
  async getGroupMembers(groupId: string): Promise<GroupMemberInfo[]> {
    const user = this.getUser()
    if (!user) throw new Error('Not authenticated')
    return this.fetchJson<GroupMemberInfo[]>(
      `${this.duarUrl}/workspaces/${user.workspaceId}/groups/${groupId}/members`,
    )
  }

  /** Get current user's full profile (includes avatar_url). */
  async getProfile(): Promise<UserProfile> {
    return this.fetchJson<UserProfile>(`${this.duarUrl}/users/me`)
  }

  // ── Private ───────────────────────────────────────────────────────

  private notify(): void {
    const user = this.getUser()
    for (const cb of this.listeners) {
      try { cb(user) } catch { /* ignore listener errors */ }
    }
  }

  private scheduleRefresh(): void {
    this.clearRefreshTimer()
    const token = this.store.getAuthzToken()
    if (!token) return

    try {
      const parts = token.split('.')
      const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')))
      const expiresAt = payload.exp * 1000
      const delay = expiresAt - Date.now() - this.refreshBuffer * 1000
      if (delay <= 0) {
        void this.refresh()
        return
      }
      this.refreshTimer = setTimeout(() => void this.refresh(), delay)
    } catch {
      // Can't parse token, skip scheduling
    }
  }

  private clearRefreshTimer(): void {
    if (this.refreshTimer !== null) {
      clearTimeout(this.refreshTimer)
      this.refreshTimer = null
    }
  }
}
