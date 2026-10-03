import { generateCodeVerifier, deriveCodeChallenge } from './pkce'
import { MemoryStore } from './storage'
import { isTokenExpired, tokenToUser } from './jwt-utils'
import { warnIfInsecure } from './warn-insecure'
import type {
  DuarConfig,
  DuarUser,
  TokenResponse,
  TokenStore,
  WorkspaceOption,
} from './types'

const PKCE_KEY = 'duar_pkce_verifier'
const STATE_KEY = 'duar_oauth_state'

// Cap on how long to wait for the cross-tab refresh lock before giving up and
// refreshing unlocked — a stuck lock must never wedge auth forever.
const REFRESH_LOCK_TIMEOUT_MS = 5000

// Max random amount to pull each tab's scheduled refresh *earlier*, so tabs
// sharing a store don't all fire at the same instant and pile onto the lock.
// Subtracted (never added) so a token is never left to expire.
const REFRESH_JITTER_MS = 5000

type AuthStateListener = (user: DuarUser | null) => void

interface LockManagerLike {
  request(
    name: string,
    options: { signal?: AbortSignal },
    callback: () => Promise<unknown>,
  ): Promise<unknown>
}

/**
 * Browser auth client for Duar. Handles PKCE, token storage, refresh, and
 * the non-standard workspace-selection auth flow.
 */
export class DuarAuth {
  private readonly url: string
  private readonly clientId: string
  private readonly redirectUri: string
  private readonly store: TokenStore
  private readonly autoRefresh: boolean
  private readonly refreshBuffer: number
  private refreshTimer: ReturnType<typeof setTimeout> | null = null
  private refreshPromise: Promise<boolean> | null = null
  private listeners: Set<AuthStateListener> = new Set()
  private readonly refreshLockName: string
  private channel: BroadcastChannel | null = null

  constructor(config: DuarConfig) {
    this.url = config.duarUrl.replace(/\/+$/, '')
    if (!config.clientId) {
      throw new Error(
        'DuarAuth: clientId is required — obtain it from the Duar admin panel.',
      )
    }
    this.clientId = config.clientId
    this.redirectUri =
      config.redirectUri ??
      (typeof window !== 'undefined'
        ? `${window.location.origin}/auth/callback`
        : '')
    this.store = config.storage ?? new MemoryStore()
    this.autoRefresh = config.autoRefresh ?? true
    this.refreshBuffer = config.refreshBuffer ?? 60
    // Per-app lock name so tabs of the same app coordinate but distinct apps
    // on one origin don't contend.
    this.refreshLockName = `duar:refresh:${this.clientId}`
    // Cross-tab auth events (logout / refreshed) so other tabs of this app
    // react immediately instead of only on their own next timer or request.
    if (typeof BroadcastChannel !== 'undefined') {
      this.channel = new BroadcastChannel(`duar:auth:${this.clientId}`)
      this.channel.onmessage = (e: MessageEvent) => this.onBroadcast(e.data)
    }
    warnIfInsecure(this.url, 'DuarAuth')

    // Schedule a refresh if we already have a valid token
    if (this.autoRefresh && this.store.getAccessToken()) {
      this.scheduleRefresh()
    }
  }

  private onBroadcast(msg: { type?: string } | null): void {
    // Apply the sibling tab's event locally. Never re-broadcast here, or two
    // tabs would ping-pong forever. A message may still be dispatched right
    // after destroy() closed the channel — ignore it rather than re-arm a
    // timer on a torn-down instance.
    if (!this.channel || !msg) return
    if (msg.type === 'logout') {
      this.store.clear()
      this.clearRefreshTimer()
      this.notify()
    } else if (msg.type === 'refreshed') {
      this.notify()
      if (this.autoRefresh) this.scheduleRefresh()
    }
  }

  // ── Auth flow ───────────────────────────────────────────────────────

  /** List available OAuth providers. */
  async getProviders(): Promise<string[]> {
    const res = await fetch(`${this.url}/auth/providers`)
    if (!res.ok) throw new Error('Failed to fetch providers')
    // Server wire shape is ProviderListResponse: { providers: [...] }
    const data = await res.json()
    return data.providers
  }

  /** Initiate OAuth + PKCE login. Redirects the browser. */
  async login(provider: string): Promise<void> {
    const verifier = generateCodeVerifier()
    const challenge = await deriveCodeChallenge(verifier)
    sessionStorage.setItem(PKCE_KEY, verifier)

    // Generate and store a random state parameter to prevent CSRF
    const stateBytes = new Uint8Array(16)
    crypto.getRandomValues(stateBytes)
    const state = Array.from(stateBytes, (b) => b.toString(16).padStart(2, '0')).join('')
    sessionStorage.setItem(STATE_KEY, state)

    const params = new URLSearchParams({
      client_id: this.clientId,
      redirect_uri: this.redirectUri,
      code_challenge: challenge,
      code_challenge_method: 'S256',
      state,
    })
    window.location.href = `${this.url}/auth/login/${provider}?${params}`
  }

  /**
   * Verify the OAuth state parameter from the callback URL.
   * Call this before processing the auth code. Throws if state is missing or mismatched.
   */
  verifyCallbackState(): void {
    const params = new URLSearchParams(window.location.search)
    const returnedState = params.get('state')
    const expectedState = sessionStorage.getItem(STATE_KEY)

    if (!expectedState || returnedState !== expectedState) {
      sessionStorage.removeItem(STATE_KEY)
      throw new Error('OAuth state mismatch — possible CSRF attack')
    }
    sessionStorage.removeItem(STATE_KEY)
  }

  /** Fetch available workspaces for the given auth code. */
  async getWorkspaces(code: string): Promise<WorkspaceOption[]> {
    // POST with the PKCE verifier: possession of a leaked auth code alone
    // must not disclose workspace names/slugs/roles (server enforces this).
    const codeVerifier = sessionStorage.getItem(PKCE_KEY)
    if (!codeVerifier) throw new Error('Missing PKCE code verifier')

    const res = await fetch(`${this.url}/auth/workspaces`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, code_verifier: codeVerifier }),
    })
    if (!res.ok) throw new Error('Failed to fetch workspaces')
    return res.json()
  }

  /** Complete token exchange with workspace selection + PKCE verifier. */
  async selectWorkspace(code: string, workspaceId: string): Promise<void> {
    const codeVerifier = sessionStorage.getItem(PKCE_KEY)
    if (!codeVerifier) throw new Error('Missing PKCE code verifier')

    const res = await fetch(`${this.url}/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code,
        workspace_id: workspaceId,
        code_verifier: codeVerifier,
      }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || 'Token exchange failed')
    }

    const data: TokenResponse = await res.json()
    this.store.setTokens(data.access_token, data.refresh_token)
    sessionStorage.removeItem(PKCE_KEY)
    this.notify()
    if (this.autoRefresh) this.scheduleRefresh()
  }

  /** Refresh the access token using the stored refresh token. Returns true on success.
   *
   * Single-flight across the whole origin: an in-tab promise dedupes concurrent
   * calls, and a Web Locks lock serializes tabs so only one ever sends a given
   * refresh token. Whoever holds the lock re-reads the store first and skips the
   * network entirely if another tab already rotated — replaying a consumed
   * refresh token would trip the server's reuse detection and log every tab out.
   */
  async refresh(): Promise<boolean> {
    if (this.refreshPromise) return this.refreshPromise
    const captured = this.store.getRefreshToken()
    if (!captured) return false
    this.refreshPromise = this._refreshLocked(captured).finally(() => {
      this.refreshPromise = null
    })
    return this.refreshPromise
  }

  /**
   * Serialize refresh across tabs of the origin via the Web Locks API so only
   * one tab ever sends a given refresh token.
   * - No Web Locks (SSR / old browser / insecure context): run directly; the
   *   re-read guard in _doRefresh still prevents replaying a rotated token.
   * - Lock acquisition times out (the holder is mid-refresh and hasn't rotated
   *   yet): do NOT replay our captured token unlocked — that would trip the
   *   server's reuse detection and log every tab out. Adopt an already-rotated
   *   token if one appeared, else fail soft (the caller retries).
   */
  private async _refreshLocked(captured: string): Promise<boolean> {
    const locks = (globalThis as { navigator?: { locks?: LockManagerLike } })
      .navigator?.locks
    if (!locks?.request) return this._doRefresh(captured)

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), REFRESH_LOCK_TIMEOUT_MS)
    let acquired = false
    try {
      return (await locks.request(
        this.refreshLockName,
        { signal: controller.signal },
        async () => {
          acquired = true
          clearTimeout(timer)
          return this._doRefresh(captured)
        },
      )) as boolean
    } catch (err) {
      if (acquired) throw err // failure came from _doRefresh, not acquisition
      return this._doRefresh(captured, false) // timed out → don't replay
    } finally {
      clearTimeout(timer)
    }
  }

  private async _doRefresh(captured: string, allowNetwork = true): Promise<boolean> {
    // Re-read after acquiring the lock: another tab may have rotated while we
    // waited. Pick up its result instead of replaying the (now consumed)
    // captured token.
    const current = this.store.getRefreshToken()
    if (!current) return false
    if (current !== captured) {
      this.notify()
      if (this.autoRefresh) this.scheduleRefresh()
      return true
    }
    // Reached without the lock (acquisition timed out): the holder is still
    // rotating, so replaying our captured token would trip reuse detection.
    if (!allowNetwork) return false

    try {
      const res = await fetch(`${this.url}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: current }),
      })
      if (!res.ok) {
        if (res.status === 401) {
          this.store.clear()
          this.clearRefreshTimer()
          this.notify()
        }
        return false
      }

      const data: TokenResponse = await res.json()
      this.store.setTokens(data.access_token, data.refresh_token)
      this.notify()
      if (this.autoRefresh) this.scheduleRefresh()
      this.channel?.postMessage({ type: 'refreshed' })
      return true
    } catch {
      return false
    }
  }

  /** Clear tokens and notify listeners. */
  logout(): void {
    this.store.clear()
    this.clearRefreshTimer()
    this.notify()
    this.channel?.postMessage({ type: 'logout' })
  }

  // ── Token access ──────────────────────────────────────────────────

  /** Get the current access token (may be expired). */
  getToken(): string | null {
    return this.store.getAccessToken()
  }

  /** Parse the current access token into a DuarUser, or null. */
  getUser(): DuarUser | null {
    const token = this.store.getAccessToken()
    if (!token) return null
    try {
      if (isTokenExpired(token)) return null
      return tokenToUser(token)
    } catch {
      return null
    }
  }

  /** True if a non-expired access token exists. */
  get isAuthenticated(): boolean {
    const token = this.store.getAccessToken()
    return !!token && !isTokenExpired(token)
  }

  // ── Fetch wrapper ─────────────────────────────────────────────────

  /** Fetch with automatic Bearer header and 401→refresh→retry. */
  async fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const doFetch = (token: string | null) => {
      const headers = new Headers(init?.headers)
      if (token) headers.set('Authorization', `Bearer ${token}`)
      return fetch(input, { ...init, headers })
    }

    let res = await doFetch(this.store.getAccessToken())

    if (res.status === 401) {
      const refreshed = await this.refresh()
      if (refreshed) {
        res = await doFetch(this.store.getAccessToken())
      }
    }

    return res
  }

  /** Fetch JSON with automatic Bearer header, 401 retry, and response parsing. */
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

  // ── Events ────────────────────────────────────────────────────────

  /** Subscribe to auth state changes. Returns an unsubscribe function. */
  onAuthStateChange(cb: AuthStateListener): () => void {
    this.listeners.add(cb)
    return () => {
      this.listeners.delete(cb)
    }
  }

  // ── Cleanup ───────────────────────────────────────────────────────

  /** Clean up timers and the cross-tab channel. Call when done (e.g. unmount). */
  destroy(): void {
    this.clearRefreshTimer()
    this.listeners.clear()
    this.channel?.close()
    this.channel = null
  }

  // ── Private ───────────────────────────────────────────────────────

  private notify(): void {
    const user = this.getUser()
    for (const cb of this.listeners) {
      try {
        cb(user)
      } catch {
        // ignore listener errors
      }
    }
  }

  private scheduleRefresh(): void {
    this.clearRefreshTimer()
    const token = this.store.getAccessToken()
    if (!token) return

    try {
      const parts = token.split('.')
      const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')))
      const expiresAt = payload.exp * 1000
      const delay =
        expiresAt -
        Date.now() -
        this.refreshBuffer * 1000 -
        Math.random() * REFRESH_JITTER_MS
      if (delay <= 0) {
        // Token already near expiry, refresh immediately
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
