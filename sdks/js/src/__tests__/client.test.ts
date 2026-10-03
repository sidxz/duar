import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { DuarAuth } from '../client'
import { MemoryStore } from '../storage'

// Helper to create a fake JWT
function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' }))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  const body = btoa(JSON.stringify(payload))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `${header}.${body}.fake-signature`
}

const validPayload = {
  sub: 'user-1',
  email: 'test@test.com',
  name: 'Test',
  wid: 'ws-1',
  wslug: 'test-ws',
  wrole: 'editor',
  groups: [],
  aud: 'duar:access',
  iss: 'duar',
  exp: Math.floor(Date.now() / 1000) + 3600,
  iat: Math.floor(Date.now() / 1000),
  jti: 'jti-1',
}

describe('DuarAuth', () => {
  let store: MemoryStore
  let client: DuarAuth

  beforeEach(() => {
    store = new MemoryStore()
    client = new DuarAuth({
      duarUrl: 'http://localhost:9003',
      clientId: '00000000-0000-0000-0000-000000000001',
      storage: store,
      autoRefresh: false,
    })
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    client.destroy()
    vi.restoreAllMocks()
  })

  it('getProviders unwraps the ProviderListResponse wire shape', async () => {
    // Server returns {"providers": [...]} (ProviderListResponse), never a bare array
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ providers: ['google', 'github'] }), {
        status: 200,
      }),
    )
    const providers = await client.getProviders()
    expect(providers).toEqual(['google', 'github'])
    expect(fetch).toHaveBeenCalledWith('http://localhost:9003/auth/providers')
  })

  it('getWorkspaces POSTs the code with the PKCE verifier', async () => {
    sessionStorage.setItem('duar_pkce_verifier', 'v'.repeat(43))
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify([{ id: 'ws-1', name: 'WS', slug: 'ws', role: 'editor' }]),
        { status: 200 },
      ),
    )
    const workspaces = await client.getWorkspaces('code-123')
    expect(workspaces).toHaveLength(1)
    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:9003/auth/workspaces',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ code: 'code-123', code_verifier: 'v'.repeat(43) }),
      }),
    )
    sessionStorage.removeItem('duar_pkce_verifier')
  })

  it('login constructs correct redirect URL', async () => {
    const mockLocation = { href: '' }
    vi.stubGlobal('window', {
      ...window,
      location: mockLocation,
      origin: 'http://localhost:5173',
    })

    // DuarAuth reads window.location.origin in constructor for redirectUri
    const authClient = new DuarAuth({
      duarUrl: 'http://localhost:9003',
      clientId: '00000000-0000-0000-0000-000000000001',
      redirectUri: 'http://localhost:5173/auth/callback',
      storage: store,
      autoRefresh: false,
    })

    await authClient.login('google')

    expect(mockLocation.href).toContain('http://localhost:9003/auth/login/google?')
    expect(mockLocation.href).toContain('client_id=00000000-0000-0000-0000-000000000001')
    expect(mockLocation.href).toContain('redirect_uri=')
    expect(mockLocation.href).toContain('code_challenge=')
    expect(mockLocation.href).toContain('code_challenge_method=S256')
    expect(sessionStorage.getItem('duar_pkce_verifier')).toBeTruthy()

    authClient.destroy()
  })

  it('selectWorkspace sends correct body and stores tokens', async () => {
    const accessToken = makeJwt(validPayload)
    sessionStorage.setItem('duar_pkce_verifier', 'test-verifier')

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          access_token: accessToken,
          refresh_token: 'refresh-1',
          token_type: 'bearer',
          expires_in: 3600,
        }),
        { status: 200 },
      ),
    )

    await client.selectWorkspace('auth-code', 'ws-1')

    expect(fetch).toHaveBeenCalledWith('http://localhost:9003/auth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: 'auth-code',
        workspace_id: 'ws-1',
        code_verifier: 'test-verifier',
      }),
    })

    expect(store.getAccessToken()).toBe(accessToken)
    expect(store.getRefreshToken()).toBe('refresh-1')
    expect(sessionStorage.getItem('duar_pkce_verifier')).toBeNull()
  })

  it('refresh updates stored tokens', async () => {
    const oldToken = makeJwt(validPayload)
    const newToken = makeJwt({ ...validPayload, jti: 'jti-2' })
    store.setTokens(oldToken, 'old-refresh')

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          access_token: newToken,
          refresh_token: 'new-refresh',
          token_type: 'bearer',
          expires_in: 3600,
        }),
        { status: 200 },
      ),
    )

    const success = await client.refresh()
    expect(success).toBe(true)
    expect(store.getAccessToken()).toBe(newToken)
    expect(store.getRefreshToken()).toBe('new-refresh')
  })

  it('refresh returns false when no refresh token', async () => {
    const success = await client.refresh()
    expect(success).toBe(false)
  })

  it('fetch injects Authorization header', async () => {
    const token = makeJwt(validPayload)
    store.setTokens(token, 'refresh-1')

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    )

    await client.fetch('/api/test')

    const calledInit = vi.mocked(fetch).mock.calls[0][1]!
    const headers = new Headers(calledInit.headers)
    expect(headers.get('Authorization')).toBe(`Bearer ${token}`)
  })

  it('fetch retries on 401 after successful refresh', async () => {
    const oldToken = makeJwt(validPayload)
    const newToken = makeJwt({ ...validPayload, jti: 'jti-new' })
    store.setTokens(oldToken, 'refresh-1')

    vi.mocked(fetch)
      // First call → 401
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      // Refresh call → success
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: newToken,
            refresh_token: 'new-refresh',
            token_type: 'bearer',
            expires_in: 3600,
          }),
          { status: 200 },
        ),
      )
      // Retry → success
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: 'ok' }), { status: 200 }),
      )

    const res = await client.fetch('/api/test')
    expect(res.status).toBe(200)
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('logout clears tokens and notifies listeners', () => {
    const token = makeJwt(validPayload)
    store.setTokens(token, 'refresh')

    const listener = vi.fn()
    client.onAuthStateChange(listener)

    client.logout()

    expect(store.getAccessToken()).toBeNull()
    expect(listener).toHaveBeenCalledWith(null)
  })

  it('getUser returns user from valid token', () => {
    const token = makeJwt(validPayload)
    store.setTokens(token, 'refresh')

    const user = client.getUser()
    expect(user).not.toBeNull()
    expect(user!.userId).toBe('user-1')
    expect(user!.workspaceRole).toBe('editor')
  })

  it('getUser returns null when no token', () => {
    expect(client.getUser()).toBeNull()
  })

  it('isAuthenticated reflects token state', () => {
    expect(client.isAuthenticated).toBe(false)
    store.setTokens(makeJwt(validPayload), 'refresh')
    expect(client.isAuthenticated).toBe(true)
  })
})
