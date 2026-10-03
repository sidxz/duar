import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('jose', () => ({
  createRemoteJWKSet: vi.fn(() => vi.fn()),
  jwtVerify: vi.fn(),
}))
vi.mock('@duar-auth/js/server', () => ({ verifyToken: vi.fn() }))

import { jwtVerify } from 'jose'
import { verifyToken } from '@duar-auth/js/server'
import { NextRequest } from 'next/server'
import { createDuarAuthzMiddleware } from '../authz-middleware'

const WS = '5e60ba90-4b3e-4b1a-9dcb-9d76b1a1e3a1'
const IDP_PAYLOAD = { sub: 'google|1', email: 'a@acme.com', name: 'A' }
const AUTHZ_PAYLOAD = { sub: 'u1', idp_sub: 'google|1', svc: 'my-app', wid: WS, wslug: 'acme', wrole: 'editor', actions: ['notes:create', 'notes:delete'], oid: 'org-1', oslug: 'acme', opub: false }

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.mocked(jwtVerify).mockImplementation(async (token) =>
    token === 'idp-ok' ? ({ payload: IDP_PAYLOAD } as any) : Promise.reject(new Error('bad idp')),
  )
  vi.mocked(verifyToken).mockImplementation(async (token) =>
    token === 'authz-ok' || token === 'minted' ? (AUTHZ_PAYLOAD as any) : Promise.reject(new Error('bad authz')),
  )
  fetchMock = vi.fn().mockResolvedValue(jsonResponse({ authz_token: 'minted', expires_in: 300 }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.mocked(jwtVerify).mockReset()
  vi.mocked(verifyToken).mockReset()
})

function mw(overrides: Record<string, unknown> = {}) {
  return createDuarAuthzMiddleware({
    duarUrl: 'http://duar:9003',
    idpJwksUrl: 'https://idp.example/jwks',
    idpAudience: 'client-id',
    serviceName: 'my-app',
    autoResolve: true,
    serviceKey: 'sk_test',
    idpProvider: 'google',
    ...overrides,
  })
}

function api(headers: Record<string, string>, path = '/api/items') {
  return new NextRequest(`https://app.example.com${path}`, { headers })
}

describe('createDuarAuthzMiddleware autoResolve', () => {
  it('throws when autoResolve is set without serviceKey or idpProvider', () => {
    expect(() => mw({ serviceKey: undefined })).toThrow(/serviceKey/)
    expect(() => mw({ idpProvider: undefined })).toThrow(/idpProvider/)
  })

  it('401 with a hint when neither X-Authz-Token nor X-Workspace-Id is sent', async () => {
    const res = await mw()(api({ authorization: 'Bearer idp-ok' }))
    expect(res.status).toBe(401)
    expect((await res.json()).detail).toContain('X-Workspace-Id')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('mints via /authz/resolve and forwards x-authz-token + x-duar-* headers', async () => {
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://duar:9003/authz/resolve')
    expect(init.method).toBe('POST')
    expect(init.headers['X-Service-Key']).toBe('sk_test')
    expect(JSON.parse(init.body)).toEqual({ idp_token: 'idp-ok', provider: 'google', workspace_id: WS })
    expect(res.headers.get('x-middleware-request-x-authz-token')).toBe('minted')
    expect(res.headers.get('x-middleware-request-x-duar-user-id')).toBe('u1')
    expect(res.headers.get('x-middleware-request-x-duar-workspace-id')).toBe(WS)
    expect(res.headers.get('x-middleware-request-x-duar-actions')).toBe('notes:create,notes:delete')
    expect(res.headers.get('x-middleware-request-x-duar-org-id')).toBe('org-1')
    expect(res.headers.get('x-middleware-request-x-duar-org-slug')).toBe('acme')
    expect(res.headers.get('x-middleware-request-x-duar-org-public')).toBe('false')
  })

  it('strips every client-sent x-duar-* header on public paths', async () => {
    const forged = { 'x-duar-actions': 'admin:all', 'x-duar-idp-sub': 'google|victim', 'x-duar-org-id': 'evil', 'x-duar-org-slug': 'evil', 'x-duar-org-public': 'true', 'x-duar-anything': 'evil' }
    const res = await mw({ publicPaths: ['/public'] })(api(forged, '/public'))
    expect(res.status).toBe(200)
    // Without the override, Next passes the original headers through and the nulls below prove nothing.
    expect(res.headers.get('x-middleware-override-headers')).not.toBeNull()
    for (const h of Object.keys(forged)) expect(res.headers.get(`x-middleware-request-${h}`)).toBeNull()
  })

  it('strips forged x-duar-* on the authenticated path, incl. org headers a no-org token never sets', async () => {
    vi.mocked(verifyToken).mockResolvedValueOnce({ ...AUTHZ_PAYLOAD, oid: null, oslug: null } as any)
    const forged = { 'x-duar-actions': 'admin:all', 'x-duar-org-id': 'evil', 'x-duar-org-slug': 'evil', 'x-duar-org-public': 'true', 'x-duar-anything': 'evil' }
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-authz-token': 'authz-ok', ...forged }))
    expect(res.status).toBe(200)
    const fwd = (h: string) => res.headers.get(`x-middleware-request-${h}`)
    expect(fwd('x-duar-actions')).toBe('notes:create,notes:delete')
    expect(fwd('x-duar-org-public')).toBe('false')
    for (const h of ['x-duar-org-id', 'x-duar-org-slug', 'x-duar-anything']) expect(fwd(h)).toBeNull()
  })

  it('forwards x-duar-org-public=true for a public-org token', async () => {
    vi.mocked(verifyToken).mockResolvedValueOnce({ ...AUTHZ_PAYLOAD, oslug: 'public', opub: true } as any)
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-authz-token': 'authz-ok' }))
    expect(res.headers.get('x-middleware-request-x-duar-org-public')).toBe('true')
  })

  it('second request for the same user+workspace is served from cache', async () => {
    const m = mw()
    await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    const res = await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('concurrent first requests share one mint (single-flight)', async () => {
    fetchMock.mockImplementation(
      () => new Promise((r) => setTimeout(() => r(jsonResponse({ authz_token: 'minted', expires_in: 300 })), 20)),
    )
    const m = mw()
    const req = () => m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    const results = await Promise.all([req(), req(), req()])
    expect(results.map((r) => r.status)).toEqual([200, 200, 200])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('X-Authz-Token wins over X-Workspace-Id', async () => {
    const res = await mw()(
      api({ authorization: 'Bearer idp-ok', 'x-authz-token': 'authz-ok', 'x-workspace-id': WS }),
    )
    expect(res.status).toBe(200)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('400 on a malformed X-Workspace-Id, before any Duar call', async () => {
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': 'nope' }))
    expect(res.status).toBe(400)
    expect((await res.json()).detail).toBe('Invalid X-Workspace-Id')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('an invalid IdP token never reaches Duar', async () => {
    const res = await mw()(api({ authorization: 'Bearer junk', 'x-workspace-id': WS }))
    expect(res.status).toBe(401)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    [400, 401, 'IdP token rejected by Duar: x'],
    [401, 503, 'Authorization service rejected the service key'],
    [403, 403, 'Not authorized for this workspace'],
    [409, 403, 'Not authorized for this workspace'],
    [500, 503, 'Authorization service unavailable'],
  ])('maps Duar %i to %i', async (duarStatus, expected, detail) => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'x' }, duarStatus))
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(res.status).toBe(expected)
    expect((await res.json()).detail).toBe(detail)
  })

  it('429 passes Retry-After through', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'x' }, 429, { 'Retry-After': '17' }))
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(res.status).toBe(429)
    expect(res.headers.get('retry-after')).toBe('17')
    expect((await res.json()).detail).toBe('Authorization service rate limit')
  })

  it('network failure → 503 and is not cached', async () => {
    fetchMock.mockRejectedValueOnce(new Error('ECONNREFUSED'))
    const m = mw()
    const first = await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(first.status).toBe(503)
    const second = await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(second.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('autoResolve off keeps the existing 401 even with X-Workspace-Id', async () => {
    const res = await mw({ autoResolve: false, serviceKey: undefined, idpProvider: undefined })(
      api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }),
    )
    expect(res.status).toBe(401)
    expect((await res.json()).detail).toBe('Unauthorized')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('page navigations without a Bearer token still redirect to loginPath', async () => {
    const res = await mw()(api({}, '/dashboard'))
    expect(res.status).toBe(307)
    expect(res.headers.get('location')).toBe('https://app.example.com/login')
  })

  it('auto-path failures are JSON even on page routes (the caller is an API client)', async () => {
    const res = await mw()(api({ authorization: 'Bearer idp-ok' }, '/dashboard'))
    expect(res.status).toBe(401)
    expect((await res.json()).detail).toContain('X-Workspace-Id')
  })

  it.each([
    ['non-JSON 200', () => new Response('<html>maintenance</html>', { status: 200 })],
    ['200 without authz_token', () => jsonResponse({ workspaces: [] })],
  ])('%s → 503 and nothing is cached', async (_label, make) => {
    fetchMock.mockImplementation(async () => make())
    const m = mw()
    const first = await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(first.status).toBe(503)
    expect((await first.json()).detail).toBe('Authorization service unavailable')
    await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('a minted token that fails local verification is evicted', async () => {
    fetchMock.mockImplementation(async () => jsonResponse({ authz_token: 'unverifiable', expires_in: 300 }))
    const m = mw()
    const first = await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(first.status).toBe(401)
    expect((await first.json()).detail).toBe('Invalid authz token')
    await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('an IdP token without sub never reaches Duar', async () => {
    vi.mocked(jwtVerify).mockResolvedValue({ payload: { email: 'a@acme.com' } } as any)
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(res.status).toBe(401)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
