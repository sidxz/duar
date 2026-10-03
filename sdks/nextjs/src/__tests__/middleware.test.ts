import { describe, it, expect, vi } from 'vitest'

const { PAYLOAD } = vi.hoisted(() => ({
  PAYLOAD: {
    sub: 'u1', email: 'a@acme.com', name: 'A', wid: 'ws-1', wslug: 'acme-ws', wrole: 'editor',
    groups: [], oid: 'org-1', oslug: 'acme', opub: false,
  },
}))
vi.mock('@duar-auth/js/server', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@duar-auth/js/server')>()),
  verifyToken: vi.fn(async () => PAYLOAD),
}))

import { verifyToken } from '@duar-auth/js/server'
import { NextRequest } from 'next/server'
import { createDuarMiddleware } from '../middleware'

const mw = createDuarMiddleware({ jwksUrl: 'https://duar.example/.well-known/jwks.json', publicPaths: ['/public'] })
const req = (path: string, headers: Record<string, string> = {}) =>
  new NextRequest(`https://app.example.com${path}`, { headers })
const fwd = (res: Response, h: string) => res.headers.get(`x-middleware-request-${h}`)
const FORGED = {
  'x-duar-user-id': 'evil', 'x-duar-actions': 'admin:all', 'x-duar-idp-sub': 'google|victim',
  'x-duar-org-id': 'evil', 'x-duar-org-slug': 'evil', 'x-duar-org-public': 'true', 'x-duar-anything': 'evil',
}

describe('createDuarMiddleware', () => {
  it('forwards org headers from the verified access token', async () => {
    const res = await mw(req('/api/x', { authorization: 'Bearer t' }))
    expect(fwd(res, 'x-duar-user-id')).toBe('u1')
    expect(fwd(res, 'x-duar-org-id')).toBe('org-1')
    expect(fwd(res, 'x-duar-org-slug')).toBe('acme')
    expect(fwd(res, 'x-duar-org-public')).toBe('false')
  })

  it('forwards x-duar-org-public=true for a public-org user', async () => {
    vi.mocked(verifyToken).mockResolvedValueOnce({ ...PAYLOAD, oslug: 'public', opub: true } as any)
    expect(fwd(await mw(req('/api/x', { authorization: 'Bearer t' })), 'x-duar-org-public')).toBe('true')
  })

  it('strips every client-sent x-duar-* header on public paths', async () => {
    const res = await mw(req('/public', FORGED))
    // Without the override, Next passes the original headers through and the nulls below prove nothing.
    expect(res.headers.get('x-middleware-override-headers')).not.toBeNull()
    for (const h of Object.keys(FORGED)) expect(fwd(res, h)).toBeNull()
  })

  it('strips forged headers on the authenticated path, incl. org headers a no-org user never sets', async () => {
    vi.mocked(verifyToken).mockResolvedValueOnce({ ...PAYLOAD, oid: null, oslug: null } as any)
    const res = await mw(req('/api/x', { authorization: 'Bearer t', ...FORGED }))
    expect(fwd(res, 'x-duar-user-id')).toBe('u1')
    expect(fwd(res, 'x-duar-org-public')).toBe('false')
    for (const h of ['x-duar-actions', 'x-duar-idp-sub', 'x-duar-org-id', 'x-duar-org-slug', 'x-duar-anything']) {
      expect(fwd(res, h)).toBeNull()
    }
  })
})
