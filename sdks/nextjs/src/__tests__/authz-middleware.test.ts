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
const AUTHZ_PAYLOAD = { sub: 'u1', idp_sub: 'google|1', svc: 'my-app', wid: WS, wslug: 'acme', wrole: 'editor' }

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
    [400, 401, 'IdP token rejected by Duar'],
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

  it('page routes without an authz token still redirect to loginPath', async () => {
    const res = await mw()(api({ authorization: 'Bearer idp-ok' }, '/dashboard'))
    expect(res.status).toBe(307)
    expect(res.headers.get('location')).toBe('https://app.example.com/login')
  })
})
