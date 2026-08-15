import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createDuarProxy } from '../proxy'

const WS = '5e60ba90-4b3e-4b1a-9dcb-9d76b1a1e3a1'
const GROUP = '7f10ce12-2a5f-4d2b-8e0c-1a2b3c4d5e6f'

const config = { duarUrl: 'http://duar:9003', serviceKey: 'sk_test' }

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({})))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function post(path: string[], body: unknown, headers: Record<string, string> = {}) {
  const req = new Request(`https://app.example.com/api/duar/${path.join('/')}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
  })
  return createDuarProxy(config).POST(req, { params: { path } })
}

function get(path: string[], headers: Record<string, string> = {}, search = '') {
  const req = new Request(
    `https://app.example.com/api/duar/${path.join('/')}${search}`,
    { headers },
  )
  return createDuarProxy(config).GET(req, { params: { path } })
}

describe('createDuarProxy — mint/discovery', () => {
  it('forwards POST authz/resolve with the service key injected', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ authz_token: 'eyJ...' }))
    const res = await post(['authz', 'resolve'], {
      idp_token: 'idp',
      provider: 'google',
      workspace_id: WS,
    })

    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ authz_token: 'eyJ...' })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://duar:9003/authz/resolve')
    const headers = new Headers(init.headers)
    expect(headers.get('x-service-key')).toBe('sk_test')
    expect(JSON.parse(init.body as string)).toMatchObject({ workspace_id: WS })
  })

  it('does not forward browser tokens on the mint path', async () => {
    await post(['authz', 'resolve'], { idp_token: 'x', provider: 'google' }, {
      authorization: 'Bearer stray',
      'x-authz-token': 'stray',
    })
    const headers = new Headers(fetchMock.mock.calls[0][1].headers)
    expect(headers.get('authorization')).toBeNull()
    expect(headers.get('x-authz-token')).toBeNull()
  })

  it('passes upstream error status through', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'not a member' }, 403))
    const res = await post(['authz', 'resolve'], { idp_token: 'x', provider: 'google' })
    expect(res.status).toBe(403)
    expect(await res.json()).toEqual({ detail: 'not a member' })
  })

  it('returns 502 when Duar is unreachable', async () => {
    fetchMock.mockRejectedValue(new TypeError('fetch failed'))
    const res = await post(['authz', 'resolve'], { idp_token: 'x', provider: 'google' })
    expect(res.status).toBe(502)
  })
})

describe('createDuarProxy — directory reads', () => {
  it('forwards members with tokens, query, XFF and UA — but no service key', async () => {
    await get(
      ['workspaces', WS, 'members'],
      {
        authorization: 'Bearer idp-token',
        'x-authz-token': 'authz-token',
        'x-forwarded-for': '203.0.113.7',
        'user-agent': 'TestBrowser/1.0',
      },
      '?q=jan&limit=10',
    )

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(`http://duar:9003/workspaces/${WS}/members?q=jan&limit=10`)
    const headers = new Headers(init.headers)
    expect(headers.get('authorization')).toBe('Bearer idp-token')
    expect(headers.get('x-authz-token')).toBe('authz-token')
    expect(headers.get('x-forwarded-for')).toBe('203.0.113.7')
    expect(headers.get('user-agent')).toBe('TestBrowser/1.0')
    expect(headers.get('x-service-key')).toBeNull()
  })

  it('forwards groups, group members, and users/me', async () => {
    expect((await get(['workspaces', WS, 'groups'])).status).toBe(200)
    expect((await get(['workspaces', WS, 'groups', GROUP, 'members'])).status).toBe(200)
    expect((await get(['users', 'me'])).status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('supports Next 15 promise-style params', async () => {
    const req = new Request('https://app.example.com/api/duar/users/me')
    const res = await createDuarProxy(config).GET(req, {
      params: Promise.resolve({ path: ['users', 'me'] }),
    })
    expect(res.status).toBe(200)
  })
})

describe('createDuarProxy — allowlist', () => {
  it.each([
    ['GET', ['permissions', 'register']],
    ['GET', ['admin', 'users']],
    ['GET', ['authz', 'resolve']], // wrong method
    ['GET', ['workspaces', '..', 'members']], // traversal-shaped segment
    ['GET', ['workspaces', WS, 'members', 'extra']],
  ])('rejects %s /%s with 404 and never contacts Duar', async (_m, path) => {
    const res = await get(path as string[])
    expect(res.status).toBe(404)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects POST to non-resolve paths', async () => {
    const res = await post(['users', 'me'], {})
    expect(res.status).toBe(404)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
