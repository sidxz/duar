import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('jose', () => ({
  createRemoteJWKSet: vi.fn(() => vi.fn()),
  jwtVerify: vi.fn(),
}))

import { verifyM2mToken, fetchWhoami } from '../m2m'
import { jwtVerify } from 'jose'

const JWKS = 'http://localhost:9010/.well-known/jwks.json'

function mockM2mPayload(over: Record<string, unknown> = {}) {
  vi.mocked(jwtVerify).mockResolvedValue({
    payload: {
      iss: 'http://localhost:9010',
      aud: 'sentinel:m2m',
      type: 'm2m',
      svc: 'acme-suite',
      caller: 'docs',
      actions: ['*'],
      aud_target: null,
      exp: Math.floor(Date.now() / 1000) + 300,
      iat: Math.floor(Date.now() / 1000),
      ...over,
    } as any,
    protectedHeader: { alg: 'RS256' },
    key: {} as any,
  } as any)
}

describe('verifyM2mToken', () => {
  beforeEach(() => vi.clearAllMocks())

  it('returns a SystemAuth for a valid token', async () => {
    mockM2mPayload()
    const sys = await verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' })
    expect(sys.caller).toBe('docs')
    expect(sys.svc).toBe('acme-suite')
    expect(sys.can('anything')).toBe(true)
  })

  it('verifies against the sentinel:m2m audience', async () => {
    mockM2mPayload()
    await verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' })
    expect(jwtVerify).toHaveBeenCalledWith(
      'tok', expect.anything(), expect.objectContaining({ audience: 'sentinel:m2m' }),
    )
  })

  it('rejects a token minted for another realm', async () => {
    mockM2mPayload({ svc: 'other-realm' })
    await expect(
      verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' }),
    ).rejects.toThrow(/realm/i)
  })

  it('rejects a non-m2m token type', async () => {
    mockM2mPayload({ type: 'authz' })
    await expect(
      verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' }),
    ).rejects.toThrow(/m2m/i)
  })

  it('honors aud_target when set', async () => {
    mockM2mPayload({ aud_target: 'billing' })
    await expect(
      verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite', serviceName: 'reports' }),
    ).rejects.toThrow(/target/i)
  })

  it('narrows actions: specific action allowed/denied', async () => {
    mockM2mPayload({ actions: ['reports:read'] })
    const sys = await verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' })
    expect(sys.can('reports:read')).toBe(true)
    expect(sys.can('reports:write')).toBe(false)
  })
})

describe('fetchWhoami', () => {
  beforeEach(() => vi.clearAllMocks())

  it('GETs /realm/whoami with the service key', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ service_name: 'docs', effective_scope: 'acme-suite', realm: { slug: 'acme-suite', name: 'Acme' } }), { status: 200 }),
    ))
    const who = await fetchWhoami({ duarUrl: 'http://localhost:9010', serviceKey: 'k' })
    expect(who.effective_scope).toBe('acme-suite')
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('http://localhost:9010/realm/whoami')
    expect((init?.headers as Record<string, string>)['X-Service-Key']).toBe('k')
    vi.restoreAllMocks()
  })
})

import { M2mTokenClient } from '../m2m'

describe('M2mTokenClient', () => {
  beforeEach(() => vi.clearAllMocks())

  it('mints then serves from cache within the TTL window', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ token: 'tok-1', expires_in: 300 }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const c = new M2mTokenClient('http://localhost:9010', 'k')
    expect(await c.getToken()).toBe('tok-1')
    expect(await c.getToken()).toBe('tok-1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:9010/realm/m2m-token')
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>)['X-Service-Key']).toBe('k')
    vi.restoreAllMocks()
  })

  it('dedupes concurrent mints into a single request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ token: 'tok-1', expires_in: 300 }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const c = new M2mTokenClient('http://localhost:9010', 'k')
    const tokens = await Promise.all([c.getToken(), c.getToken(), c.getToken()])
    expect(tokens).toEqual(['tok-1', 'tok-1', 'tok-1'])
    expect(fetchMock).toHaveBeenCalledTimes(1)
    vi.restoreAllMocks()
  })

  it('recovers after a failed mint (does not cache the rejection)', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('nope', { status: 403 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ token: 'tok-2', expires_in: 300 }), { status: 200 }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const c = new M2mTokenClient('http://localhost:9010', 'k')
    await expect(c.getToken()).rejects.toThrow(/403/)
    expect(await c.getToken()).toBe('tok-2')
    vi.restoreAllMocks()
  })

  it('throws when Duar rejects the mint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'not a realm member' }), { status: 403 }),
    ))
    const c = new M2mTokenClient('http://localhost:9010', 'k')
    await expect(c.getToken()).rejects.toThrow(/403/)
    vi.restoreAllMocks()
  })
})
