import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { DuarAuthz } from '../authz-client'
import { AuthzMemoryStore, AuthzLocalStorageStore } from '../authz-storage'

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' }))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  const body = btoa(JSON.stringify(payload))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `${header}.${body}.fake-signature`
}

const authzPayload = {
  sub: 'user-1',
  wid: 'ws-1',
  wslug: 'acme',
  wrole: 'editor',
  idp_sub: 'google|123',
  svc: 'notes',
  actions: ['notes:create'],
  aud: 'sentinel:authz',
  iss: 'duar',
  exp: Math.floor(Date.now() / 1000) + 300,
  iat: Math.floor(Date.now() / 1000),
  jti: 'jti-authz-1',
  type: 'authz',
}

const resolveResponse = {
  user: { id: 'user-1', email: 'alice@acme.com', name: 'Alice' },
  workspaces: [
    { id: 'ws-1', name: 'Acme Corp', slug: 'acme', role: 'editor' },
  ],
}

const selectResponse = {
  user: { id: 'user-1', email: 'alice@acme.com', name: 'Alice' },
  workspace: { id: 'ws-1', slug: 'acme', role: 'editor' },
  authz_token: makeJwt(authzPayload),
  expires_in: 300,
}

describe('DuarAuthz', () => {
  let store: AuthzMemoryStore
  let client: DuarAuthz

  beforeEach(() => {
    store = new AuthzMemoryStore()
    client = new DuarAuthz({
      duarUrl: 'http://localhost:9003',
      mintEndpoint: '/api/auth/mint',
      storage: store,
      autoRefresh: false,
    })
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    client.destroy()
    vi.restoreAllMocks()
  })

  it('resolve calls POST /auth/resolve with idp token and provider', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(resolveResponse), { status: 200 }),
    )
    const result = await client.resolve('idp-token-123', 'google')
    expect(fetch).toHaveBeenCalledWith('http://localhost:9003/authz/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idp_token: 'idp-token-123', provider: 'google' }),
    })
    expect(result.workspaces).toHaveLength(1)
    expect(result.workspaces![0].slug).toBe('acme')
  })

  it('selectWorkspace POSTs to mintEndpoint (not Duar) and stores tokens', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(selectResponse), { status: 200 }),
    )
    const listener = vi.fn()
    client.onAuthStateChange(listener)
    await client.selectWorkspace('idp-token-123', 'google', 'ws-1')

    // Credential issuance goes through the backend mint route, never the
    // browser-direct /authz/resolve path (Duar would 403 that anyway).
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('/api/auth/mint')
    expect(init?.method).toBe('POST')
    expect(init?.credentials).toBe('same-origin')
    expect(JSON.parse(init?.body as string)).toEqual({
      idp_token: 'idp-token-123',
      provider: 'google',
      workspace_id: 'ws-1',
    })

    expect(store.getIdpToken()).toBe('idp-token-123')
    expect(store.getAuthzToken()).toBe(selectResponse.authz_token)
    expect(store.getProvider()).toBe('google')
    expect(store.getWorkspaceId()).toBe('ws-1')
    expect(store.getUserIdentity()).toEqual({ email: 'alice@acme.com', name: 'Alice' })
    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener.mock.calls[0][0]).not.toBeNull()
  })

  it('selectWorkspace forwards the login-flow nonce to the mint endpoint', async () => {
    sessionStorage.setItem('duar_authz_nonce', 'the-login-nonce')
    try {
      vi.mocked(fetch).mockResolvedValueOnce(
        new Response(JSON.stringify(selectResponse), { status: 200 }),
      )
      await client.selectWorkspace('idp-token-123', 'google', 'ws-1')
      const [, init] = vi.mocked(fetch).mock.calls[0]
      expect(JSON.parse(init?.body as string).nonce).toBe('the-login-nonce')
    } finally {
      sessionStorage.removeItem('duar_authz_nonce')
    }
  })

  it('selectWorkspace clears the in-flight silent re-auth marker on success', async () => {
    sessionStorage.setItem('duar_authz_silent', '1')
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(selectResponse), { status: 200 }),
    )
    await client.selectWorkspace('idp-token-123', 'google', 'ws-1')
    expect(sessionStorage.getItem('duar_authz_silent')).toBeNull()
  })

  it('constructor rejects missing mintEndpoint', () => {
    expect(() =>
      new DuarAuthz({
        duarUrl: 'http://localhost:9003',
        // @ts-expect-error — intentionally missing required field
        mintEndpoint: undefined,
      }),
    ).toThrow(/mintEndpoint is required/)
  })

  it('getUser returns user from authz token with cached identity', () => {
    const authzToken = makeJwt(authzPayload)
    store.setUserIdentity({ email: 'alice@acme.com', name: 'Alice' })
    store.setTokens('idp-token', authzToken, 'google', 'ws-1')
    const user = client.getUser()
    expect(user).not.toBeNull()
    expect(user!.userId).toBe('user-1')
    expect(user!.email).toBe('alice@acme.com')
    expect(user!.name).toBe('Alice')
    expect(user!.workspaceRole).toBe('editor')
    expect(user!.groups).toEqual([])
  })

  it('getUser returns empty strings for identity when not cached', () => {
    const authzToken = makeJwt(authzPayload)
    store.setTokens('idp-token', authzToken, 'google', 'ws-1')
    const user = client.getUser()
    expect(user).not.toBeNull()
    expect(user!.userId).toBe('user-1')
    expect(user!.email).toBe('')
    expect(user!.name).toBe('')
    expect(user!.workspaceRole).toBe('editor')
  })

  it('getUser returns null when no authz token', () => {
    expect(client.getUser()).toBeNull()
  })

  it('getUser returns null when authz token is expired', () => {
    const expired = makeJwt({ ...authzPayload, exp: Math.floor(Date.now() / 1000) - 60 })
    store.setTokens('idp-token', expired, 'google', 'ws-1')
    expect(client.getUser()).toBeNull()
  })

  it('getHeaders returns both Authorization and X-Authz-Token', () => {
    const authzToken = makeJwt(authzPayload)
    store.setTokens('idp-token', authzToken, 'google', 'ws-1')
    const headers = client.getHeaders()
    expect(headers).toEqual({
      Authorization: 'Bearer idp-token',
      'X-Authz-Token': authzToken,
    })
  })

  it('getHeaders returns empty object when not authenticated', () => {
    expect(client.getHeaders()).toEqual({})
  })

  it('fetch injects both headers', async () => {
    const authzToken = makeJwt(authzPayload)
    store.setTokens('idp-token', authzToken, 'google', 'ws-1')
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    )
    await client.fetch('/api/notes')
    const calledInit = vi.mocked(fetch).mock.calls[0][1]!
    const headers = new Headers(calledInit.headers)
    expect(headers.get('Authorization')).toBe('Bearer idp-token')
    expect(headers.get('X-Authz-Token')).toBe(authzToken)
  })

  it('fetch retries on 401 after successful re-resolve', async () => {
    const authzToken = makeJwt(authzPayload)
    const newAuthzToken = makeJwt({ ...authzPayload, jti: 'jti-new' })
    store.setTokens('idp-token', authzToken, 'google', 'ws-1')
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          ...selectResponse,
          authz_token: newAuthzToken,
        }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: 'ok' }), { status: 200 }),
      )
    const res = await client.fetch('/api/notes')
    expect(res.status).toBe(200)
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('logout clears tokens and notifies listeners', () => {
    const authzToken = makeJwt(authzPayload)
    store.setTokens('idp-token', authzToken, 'google', 'ws-1')
    const listener = vi.fn()
    client.onAuthStateChange(listener)
    client.logout()
    expect(store.getIdpToken()).toBeNull()
    expect(store.getAuthzToken()).toBeNull()
    expect(listener).toHaveBeenCalledWith(null)
  })

  it('isAuthenticated reflects token state', () => {
    expect(client.isAuthenticated).toBe(false)
    const authzToken = makeJwt(authzPayload)
    store.setTokens('idp-token', authzToken, 'google', 'ws-1')
    expect(client.isAuthenticated).toBe(true)
  })

  it('resolve throws on HTTP error', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Invalid IdP token' }), { status: 401 }),
    )
    await expect(client.resolve('bad-token', 'google'))
      .rejects.toThrow('Invalid IdP token')
  })
})

// ── Reload / zombie-session fix (Option A: honest auth state) ──────────────
//
// On a page reload the authz token survives in localStorage but the IdP token
// (memory-only by design) is gone. Auth state must reflect that the session
// can no longer authenticate requests, instead of reporting a healthy "true"
// that 401s on every call.
describe('DuarAuthz — auth state when IdP token is absent (reload)', () => {
  const config = {
    duarUrl: 'http://localhost:9003',
    mintEndpoint: '/api/auth/mint',
    autoRefresh: false,
    redirectUri: 'http://localhost:3000/auth/callback',
    idps: { google: { clientId: 'gid', authorizationUrl: 'https://accounts.google.com/o/oauth2/v2/auth' } },
  }

  beforeEach(() => {
    // happy-dom doesn't ship a usable localStorage/sessionStorage global here;
    // back them with plain records (mirrors authz-storage.test.ts).
    const ls: Record<string, string> = {}
    const ss: Record<string, string> = {}
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => ls[k] ?? null,
      setItem: (k: string, v: string) => { ls[k] = v },
      removeItem: (k: string) => { delete ls[k] },
    })
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => ss[k] ?? null,
      setItem: (k: string, v: string) => { ss[k] = v },
      removeItem: (k: string) => { delete ss[k] },
    })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  /** Simulate a reload: persist tokens, then construct a fresh store/client so
   *  the in-memory IdP token is gone but localStorage survives. */
  function reloaded() {
    const seed = new AuthzLocalStorageStore()
    seed.setUserIdentity({ email: 'alice@acme.com', name: 'Alice' })
    seed.setTokens('idp-token', makeJwt(authzPayload), 'google', 'ws-1')
    const store = new AuthzLocalStorageStore() // fresh instance == page reload
    const client = new DuarAuthz({ ...config, storage: store })
    return { client, store }
  }

  it('getAuthState() is "needs_reauth" after reload (authz present, IdP gone)', () => {
    const { client, store } = reloaded()
    expect(store.getAuthzToken()).not.toBeNull()
    expect(store.getIdpToken()).toBeNull()
    expect(client.getAuthState()).toBe('needs_reauth')
  })

  it('isAuthenticated is false after reload (not a zombie true)', () => {
    const { client } = reloaded()
    expect(client.isAuthenticated).toBe(false)
  })

  it('getUser() returns null after reload', () => {
    const { client } = reloaded()
    expect(client.getUser()).toBeNull()
  })

  it('getHeaders() returns {} after reload', () => {
    const { client } = reloaded()
    expect(client.getHeaders()).toEqual({})
  })

  it('getAuthState() is "unauthenticated" when no authz token exists', () => {
    const client = new DuarAuthz({ ...config, storage: new AuthzLocalStorageStore() })
    expect(client.getAuthState()).toBe('unauthenticated')
    expect(client.getUser()).toBeNull()
  })

  it('getAuthState() is "unauthenticated" when the authz token is expired (even if IdP present)', () => {
    const store = new AuthzMemoryStore()
    store.setTokens('idp-token', makeJwt({ ...authzPayload, exp: Math.floor(Date.now() / 1000) - 60 }), 'google', 'ws-1')
    const client = new DuarAuthz({ ...config, storage: store })
    expect(client.getAuthState()).toBe('unauthenticated')
  })

  it('getAuthState() is "authenticated" when both tokens are present and valid', () => {
    const store = new AuthzMemoryStore()
    store.setTokens('idp-token', makeJwt(authzPayload), 'google', 'ws-1')
    const client = new DuarAuthz({ ...config, storage: store })
    expect(client.getAuthState()).toBe('authenticated')
    expect(client.isAuthenticated).toBe(true)
    expect(client.getUser()).not.toBeNull()
  })
})

// ── Silent re-auth (Option B): prompt=none full-page redirect ──────────────
describe('DuarAuthz — silentLogin', () => {
  let ss: Record<string, string>
  const START_URL = 'http://localhost:3000/compounds?page=2'

  const config = {
    duarUrl: 'http://localhost:9003',
    mintEndpoint: '/api/auth/mint',
    autoRefresh: false,
    redirectUri: 'http://localhost:3000/auth/callback',
    idps: { google: { clientId: 'gid', authorizationUrl: 'https://accounts.google.com/o/oauth2/v2/auth' } },
  }

  beforeEach(() => {
    ss = {}
    const ls: Record<string, string> = {}
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => ls[k] ?? null,
      setItem: (k: string, v: string) => { ls[k] = v },
      removeItem: (k: string) => { delete ls[k] },
    })
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => ss[k] ?? null,
      setItem: (k: string, v: string) => { ss[k] = v },
      removeItem: (k: string) => { delete ss[k] },
    })
    // Use happy-dom's native location; set a deterministic same-origin start.
    window.location.href = START_URL
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  /** A reloaded client: authz token + provider/identity persisted, IdP token gone. */
  function reloadedClient() {
    const seed = new AuthzLocalStorageStore()
    seed.setUserIdentity({ email: 'alice@acme.com', name: 'Alice' })
    seed.setTokens('idp-token', makeJwt(authzPayload), 'google', 'ws-1')
    return new DuarAuthz({ ...config, storage: new AuthzLocalStorageStore() })
  }

  it('redirects to the IdP with prompt=none and a fresh nonce', () => {
    const client = reloadedClient()
    const started = client.silentLogin()
    expect(started).toBe(true)
    const url = new URL(window.location.href)
    expect(url.origin + url.pathname).toBe('https://accounts.google.com/o/oauth2/v2/auth')
    expect(url.searchParams.get('prompt')).toBe('none')
    expect(url.searchParams.get('redirect_uri')).toBe('http://localhost:3000/auth/callback')
    expect(url.searchParams.get('client_id')).toBe('gid')
    const nonce = url.searchParams.get('nonce')
    expect(nonce).toBeTruthy()
    // The same nonce must be persisted so handleCallback can verify it (replay protection).
    expect(sessionStorage.getItem('duar_authz_nonce')).toBe(nonce)
  })

  it('passes login_hint from the cached identity to target the right account', () => {
    const client = reloadedClient()
    client.silentLogin()
    const url = new URL(window.location.href)
    expect(url.searchParams.get('login_hint')).toBe('alice@acme.com')
  })

  it('keeps prompt=none even when the IdP is configured with extraParams.prompt', () => {
    // A common Google config forces the account chooser via prompt=select_account;
    // that must NOT leak into the silent flow and turn it interactive.
    const seed = new AuthzLocalStorageStore()
    seed.setTokens('idp-token', makeJwt(authzPayload), 'google', 'ws-1')
    const client = new DuarAuthz({
      ...config,
      storage: new AuthzLocalStorageStore(),
      idps: {
        google: {
          clientId: 'gid',
          authorizationUrl: 'https://accounts.google.com/o/oauth2/v2/auth',
          extraParams: { prompt: 'select_account', access_type: 'offline' },
        },
      },
    })
    expect(client.silentLogin()).toBe(true)
    const url = new URL(window.location.href)
    expect(url.searchParams.get('prompt')).toBe('none') // silent flag wins
    expect(url.searchParams.get('access_type')).toBe('offline') // other extras still applied
  })

  it('stores the current path as the post-reauth return target', () => {
    const client = reloadedClient()
    client.silentLogin()
    expect(sessionStorage.getItem('duar_authz_return_to')).toBe('/compounds?page=2')
  })

  it('uses the stored provider when none is passed', () => {
    const client = reloadedClient()
    client.silentLogin()
    expect(sessionStorage.getItem('duar_authz_provider')).toBe('google')
  })

  it('returns false and does NOT redirect when no provider can be resolved', () => {
    // No stored provider, none passed.
    const client = new DuarAuthz({ ...config, storage: new AuthzLocalStorageStore() })
    expect(client.silentLogin()).toBe(false)
    expect(window.location.href).toBe(START_URL) // no navigation
  })

  it('is a no-op (loop guard) when a silent attempt is already in flight', () => {
    const client = reloadedClient()
    expect(client.silentLogin()).toBe(true)
    const afterFirst = window.location.href
    expect(client.silentLogin()).toBe(false) // second call blocked by the inflight marker
    expect(window.location.href).toBe(afterFirst) // no second navigation
  })

  it('throws when the resolved provider is not configured in idps', () => {
    const seed = new AuthzLocalStorageStore()
    seed.setTokens('idp-token', makeJwt(authzPayload), 'github', 'ws-1') // provider not in idps
    const client = new DuarAuthz({ ...config, storage: new AuthzLocalStorageStore() })
    expect(() => client.silentLogin()).toThrow(/github/)
    expect(window.location.href).toBe(START_URL) // no navigation
  })
})

// ── handleCallback: silent-failure path + return-to plumbing ───────────────
describe('DuarAuthz — handleCallback (silent reauth + return-to)', () => {
  let ss: Record<string, string>

  const config = {
    duarUrl: 'http://localhost:9003',
    mintEndpoint: '/api/auth/mint',
    autoRefresh: false,
    redirectUri: 'http://localhost:3000/auth/callback',
    idps: { google: { clientId: 'gid', authorizationUrl: 'https://accounts.google.com/o/oauth2/v2/auth' } },
  }

  function client() {
    return new DuarAuthz({ ...config, storage: new AuthzMemoryStore() })
  }

  /** Drive happy-dom's native location to the callback URL with the given fragment. */
  function setHash(hash: string) {
    window.location.href = `http://localhost:3000/auth/callback${hash}`
  }

  beforeEach(() => {
    ss = {}
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => ss[k] ?? null,
      setItem: (k: string, v: string) => { ss[k] = v },
      removeItem: (k: string) => { delete ss[k] },
    })
    window.location.href = 'http://localhost:3000/auth/callback'
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns status "silent_failed" (not a throw) when the IdP returns login_required during a silent attempt', () => {
    ss['duar_authz_nonce'] = 'n1'
    ss['duar_authz_provider'] = 'google'
    ss['duar_authz_silent'] = '1'
    ss['duar_authz_return_to'] = '/compounds'
    setHash('#error=login_required&error_description=login_required')
    const result = client().handleCallback()
    expect(result).not.toBeNull()
    expect(result!.status).toBe('silent_failed')
    if (result!.status === 'silent_failed') {
      expect(result!.returnTo).toBe('/compounds')
    }
    // The inflight marker must be consumed so the app does not loop.
    expect(sessionStorage.getItem('duar_authz_silent')).toBeNull()
  })

  it('returns silent_failed for a NON-interaction error (e.g. access_denied) during a silent attempt', () => {
    // Not just login_required/interaction_required/consent_required — ANY IdP
    // error during a silent attempt must degrade to interactive, never throw
    // (a throw under autoReauth loops on every reload).
    ss['duar_authz_nonce'] = 'n1'
    ss['duar_authz_provider'] = 'google'
    ss['duar_authz_silent'] = String(Date.now())
    setHash('#error=account_selection_required&error_description=pick')
    const result = client().handleCallback()
    expect(result).not.toBeNull()
    expect(result!.status).toBe('silent_failed')
    expect(sessionStorage.getItem('duar_authz_silent')).toBeNull() // marker consumed
  })

  it('still throws on a genuine error when NOT a silent attempt', () => {
    ss['duar_authz_nonce'] = 'n1'
    setHash('#error=access_denied&error_description=denied')
    expect(() => client().handleCallback()).toThrow(/denied|access_denied/)
  })

  it('returns status "success" with idpToken, provider, and returnTo on a valid callback', () => {
    ss['duar_authz_nonce'] = 'n1'
    ss['duar_authz_provider'] = 'google'
    ss['duar_authz_return_to'] = '/compounds?page=2'
    const idToken = makeJwt({ nonce: 'n1', sub: 'google|123' })
    setHash(`#id_token=${idToken}`)
    const result = client().handleCallback()
    expect(result).not.toBeNull()
    expect(result!.status).toBe('success')
    if (result!.status === 'success') {
      expect(result!.idpToken).toBe(idToken)
      expect(result!.provider).toBe('google')
      expect(result!.returnTo).toBe('/compounds?page=2')
    }
  })

  it('leaves the silent marker set on success so a re-entrant silentLogin no-ops until selectWorkspace', () => {
    // React fires child (callback) effects before parent (provider) effects.
    // If handleCallback cleared the silent marker on success, the provider's
    // autoReauth could fire a SECOND redirect during the async resolve window.
    ss['duar_authz_nonce'] = 'n1'
    ss['duar_authz_provider'] = 'google'
    ss['duar_authz_silent'] = '1'
    const idToken = makeJwt({ nonce: 'n1', sub: 'google|123' })
    setHash(`#id_token=${idToken}`)
    const result = client().handleCallback()
    expect(result!.status).toBe('success')
    expect(sessionStorage.getItem('duar_authz_silent')).toBe('1') // deferred — NOT cleared here
    expect(sessionStorage.getItem('duar_authz_nonce')).toBeNull() // replay nonce IS consumed
  })

  it('uses an explicitly passed captured hash (React StrictMode pre-capture)', () => {
    ss['duar_authz_nonce'] = 'n1'
    ss['duar_authz_provider'] = 'google'
    setHash('') // URL hash already cleaned at module load; window hash is empty
    const idToken = makeJwt({ nonce: 'n1', sub: 'google|123' })
    const result = client().handleCallback(`id_token=${idToken}`)
    expect(result).not.toBeNull()
    expect(result!.status).toBe('success')
    if (result!.status === 'success') {
      expect(result!.idpToken).toBe(idToken)
    }
  })
})

// ── consumeReturnTo: open-redirect-safe return path ────────────────────────
describe('DuarAuthz — consumeReturnTo', () => {
  let ss: Record<string, string>
  const config = {
    duarUrl: 'http://localhost:9003',
    mintEndpoint: '/api/auth/mint',
    autoRefresh: false,
  }
  function client() {
    return new DuarAuthz({ ...config, storage: new AuthzMemoryStore() })
  }
  beforeEach(() => {
    ss = {}
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => ss[k] ?? null,
      setItem: (k: string, v: string) => { ss[k] = v },
      removeItem: (k: string) => { delete ss[k] },
    })
  })
  afterEach(() => { vi.unstubAllGlobals() })

  it('returns and clears a stored same-origin path', () => {
    ss['duar_authz_return_to'] = '/compounds?page=2'
    const c = client()
    expect(c.consumeReturnTo()).toBe('/compounds?page=2')
    expect(sessionStorage.getItem('duar_authz_return_to')).toBeNull() // cleared
  })

  it('returns null when nothing is stored', () => {
    expect(client().consumeReturnTo()).toBeNull()
  })

  it('rejects a protocol-relative URL (open-redirect guard)', () => {
    ss['duar_authz_return_to'] = '//evil.example.com/phish'
    expect(client().consumeReturnTo()).toBeNull()
  })

  it('rejects an absolute URL with a scheme (open-redirect guard)', () => {
    ss['duar_authz_return_to'] = 'https://evil.example.com'
    expect(client().consumeReturnTo()).toBeNull()
  })

  it('rejects a value that does not start with /', () => {
    ss['duar_authz_return_to'] = 'compounds'
    expect(client().consumeReturnTo()).toBeNull()
  })
})

describe('DuarAuthz with a relative (same-origin proxy) duarUrl', () => {
  // Private-network deployments: the browser cannot reach Duar, so
  // duarUrl points at the app's own reverse-proxy mount (e.g. the
  // @duar-auth/nextjs createDuarProxy or Python SDK proxy_router).
  // These lock in that every browser call stays same-origin.
  let store: AuthzMemoryStore
  let client: DuarAuthz

  beforeEach(() => {
    store = new AuthzMemoryStore()
    client = new DuarAuthz({
      duarUrl: '/api/duar',
      mintEndpoint: '/api/duar/authz/resolve',
      storage: store,
      autoRefresh: false,
    })
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    client.destroy()
    vi.restoreAllMocks()
  })

  it('resolve posts to the proxy path, not an absolute URL', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(resolveResponse), { status: 200 }),
    )
    await client.resolve('idp-token-123', 'google')
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/duar/authz/resolve')
  })

  it('directory reads hit the proxy path with dual-token headers', async () => {
    store.setTokens('idp-token', makeJwt(authzPayload), 'google', 'ws-1')
    store.setUserIdentity({ email: 'alice@acme.com', name: 'Alice' })
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify([]), { status: 200 }),
    )
    await client.searchMembers('jan', 10)
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('/api/duar/workspaces/ws-1/members?q=jan&limit=10')
    const headers = new Headers((init as RequestInit).headers)
    expect(headers.get('X-Authz-Token')).toBe(makeJwt(authzPayload))
  })
})
