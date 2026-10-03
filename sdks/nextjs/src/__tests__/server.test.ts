import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { SignJWT, exportJWK, generateKeyPair, type KeyLike } from 'jose'

vi.mock('next/headers', () => ({ headers: vi.fn(), cookies: vi.fn() }))

import { cookies, headers } from 'next/headers'
import { getUser, createDuarServer, createDuarAuthzServer } from '../server'

const BASE = { 'x-duar-user-id': 'u1', 'x-duar-workspace-id': 'ws-1' }
async function userFrom(h: Record<string, string>) {
  vi.mocked(headers).mockResolvedValueOnce(new Headers(h) as any)
  return getUser()
}

describe('getUser', () => {
  it('returns null without the middleware-set identity headers', async () => {
    expect(await userFrom({ 'x-duar-user-id': 'u1' })).toBeNull()
  })

  it('parses x-duar-actions: absent → undefined (standard mode), empty → [], list → array', async () => {
    expect((await userFrom(BASE))!.actions).toBeUndefined()
    expect((await userFrom({ ...BASE, 'x-duar-actions': '' }))!.actions).toEqual([])
    expect((await userFrom({ ...BASE, 'x-duar-actions': 'notes:create,notes:delete' }))!.actions)
      .toEqual(['notes:create', 'notes:delete'])
  })

  it('org: absent → null; orgIsPublic only for the exact string "true"', async () => {
    const user = (await userFrom(BASE))!
    expect([user.orgId, user.orgSlug, user.orgIsPublic]).toEqual([null, null, false])
    for (const v of ['TRUE', '1', 'yes', '']) {
      expect((await userFrom({ ...BASE, 'x-duar-org-public': v }))!.orgIsPublic).toBe(false)
    }
    expect((await userFrom({ ...BASE, 'x-duar-org-public': 'true' }))!.orgIsPublic).toBe(true)
  })
})

// The verifying helpers get real RS256 tokens checked against JWKS served by a local server.
let base = ''
let duarKey: KeyLike
let idpKey: KeyLike
const requested: string[] = []
const jwksBodies: Record<string, string> = {}
const jwksServer = createServer((req, res) => {
  requested.push(req.url!)
  const body = jwksBodies[req.url!]
  res.writeHead(body ? 200 : 404, { 'content-type': 'application/json' }).end(body ?? '{}')
})

beforeAll(async () => {
  const jwks = async (key: KeyLike) =>
    JSON.stringify({ keys: [{ ...(await exportJWK(key)), kid: 'k1', alg: 'RS256', use: 'sig' }] })
  const duar = await generateKeyPair('RS256')
  const idp = await generateKeyPair('RS256')
  duarKey = duar.privateKey
  idpKey = idp.privateKey
  jwksBodies['/duar/.well-known/jwks.json'] = await jwks(duar.publicKey)
  jwksBodies['/idp/jwks'] = await jwks(idp.publicKey)
  await new Promise<void>((resolve) => jwksServer.listen(0, '127.0.0.1', resolve))
  base = `http://127.0.0.1:${(jwksServer.address() as AddressInfo).port}`
})
afterAll(() => new Promise<void>((resolve) => jwksServer.close(() => resolve())))

function request(h: Record<string, string>, cookie: Record<string, string> = {}) {
  vi.mocked(headers).mockResolvedValue(new Headers(h) as any)
  vi.mocked(cookies).mockResolvedValue({
    get: (name: string) => (cookie[name] ? { name, value: cookie[name] } : undefined),
  } as any)
}

const sign = (key: KeyLike, claims: Record<string, unknown>, aud: string, iss: string) =>
  new SignJWT(claims).setProtectedHeader({ alg: 'RS256', kid: 'k1' })
    .setAudience(aud).setIssuer(iss).setExpirationTime('5m').sign(key)


const FORGED = { 'x-duar-user-id': 'evil', 'x-duar-workspace-id': 'ws-1', 'x-duar-workspace-role': 'owner' }

describe('createDuarServer: verifies the access token itself', () => {
  const ACCESS = {
    sub: 'u1', email: 'a@acme.com', name: 'A', wid: 'ws-1', wslug: 'acme-ws', wrole: 'editor',
    groups: ['g1'], oid: 'org-1', oslug: 'acme', opub: false,
  }
  const server = (over = {}) => createDuarServer({ jwksUrl: `${base}/duar/.well-known/jwks.json`, ...over })
  const access = ({ key = duarKey, aud = 'duar:access', iss = `${base}/duar` } = {}) => sign(key, ACCESS, aud, iss)

  it('returns the user from a verified Bearer token, not from x-duar-* headers', async () => {
    request({ authorization: `Bearer ${await access()}`, ...FORGED })
    const { getUser, requireUser } = server()
    expect(await getUser()).toEqual({
      userId: 'u1', email: 'a@acme.com', name: 'A', workspaceId: 'ws-1', workspaceSlug: 'acme-ws',
      workspaceRole: 'editor', groups: ['g1'], orgId: 'org-1', orgSlug: 'acme', orgIsPublic: false,
    })
    expect((await requireUser()).userId).toBe('u1')
  })

  it('falls back to the duar_access_token cookie', async () => {
    request({}, { duar_access_token: await access() })
    expect((await server().getUser())?.userId).toBe('u1')
  })

  it('returns null, and requireUser throws, for forged x-duar-* headers without a token', async () => {
    request(FORGED)
    const { getUser, requireUser } = server()
    expect(await getUser()).toBeNull()
    await expect(requireUser()).rejects.toThrow('Unauthorized')
  })

  it.each([
    ['signed by a key Duar does not publish', () => access({ key: idpKey })],
    ['minted for another audience', () => access({ aud: 'duar:authz' })],
    ['from another issuer', () => access({ iss: 'https://evil.example' })],
  ])('returns null for a token %s', async (_, token) => {
    request({ authorization: `Bearer ${await token()}` })
    expect(await server().getUser()).toBeNull()
  })

  it('honors an issuer override (Duar reached internally, its public BASE_URL in iss)', async () => {
    request({ authorization: `Bearer ${await access({ iss: 'https://duar.example.com' })}` })
    expect((await server({ issuer: 'https://duar.example.com' }).getUser())?.userId).toBe('u1')
  })

  it('applies allowedWorkspaces', async () => {
    request({ authorization: `Bearer ${await access()}` })
    expect(await server({ allowedWorkspaces: ['ws-2'] }).getUser()).toBeNull()
    expect((await server({ allowedWorkspaces: ['ws-1'] }).getUser())?.userId).toBe('u1')
  })

  it('withAuth answers 401 without running the handler when unauthenticated', async () => {
    const handler = vi.fn(async (_req: Request, user: { userId: string }) => Response.json({ id: user.userId }))
    const route = server().withAuth(handler)
    const call = () => route(new Request('https://app.example.com/api/x'))

    request(FORGED)
    const denied = await call()
    expect(denied.status).toBe(401)
    expect(await denied.json()).toEqual({ detail: 'Unauthorized' })
    expect(handler).not.toHaveBeenCalled()

    request({ authorization: `Bearer ${await access()}` })
    expect(await (await call()).json()).toEqual({ id: 'u1' })
  })

  it('defers config errors to the first call, so a module-scope factory survives next build', async () => {
    const s = createDuarServer({ jwksUrl: undefined as unknown as string }) // env var unset at build time
    request({})
    await expect(s.getUser()).rejects.toThrow()
  })
})

describe('createDuarAuthzServer: verifies both tokens itself', () => {
  const IDP = { sub: 'google|1', email: 'a@acme.com', name: 'A' }
  const AUTHZ = {
    sub: 'u1', idp_sub: 'google|1', svc: 'my-app', wid: 'ws-1', wslug: 'acme-ws', wrole: 'editor',
    actions: ['notes:create'], oid: 'org-1', oslug: 'acme', opub: false,
  }
  // Shares the middleware's full config, auto-resolve settings included.
  const server = (over = {}) => createDuarAuthzServer({
    duarUrl: `${base}/duar`, idpJwksUrl: `${base}/idp/jwks`, idpAudience: 'client-id', idpIssuer: `${base}/idp`,
    serviceName: 'my-app', autoResolve: true, serviceKey: 'sk', idpProvider: 'google', ...over,
  })
  const idpToken = ({ aud = 'client-id', iss = `${base}/idp`, ...claims }: Record<string, unknown> = {}) =>
    sign(idpKey, { ...IDP, ...claims }, aud as string, iss as string)
  const authzToken = ({ iss = `${base}/duar`, ...claims }: Record<string, unknown> = {}) =>
    sign(duarKey, { ...AUTHZ, ...claims }, 'duar:authz', iss as string)

  it('takes identity from the IdP token and workspace, actions and org from the authz token', async () => {
    request({ authorization: `Bearer ${await idpToken()}`, 'x-authz-token': await authzToken(), ...FORGED })
    expect(await server().getUser()).toEqual({
      userId: 'u1', email: 'a@acme.com', name: 'A', workspaceId: 'ws-1', workspaceSlug: 'acme-ws',
      workspaceRole: 'editor', groups: [], actions: ['notes:create'], orgId: 'org-1', orgSlug: 'acme',
      orgIsPublic: false,
    })
  })

  it('returns null without an authz token and never mints one', async () => {
    request({ authorization: `Bearer ${await idpToken()}`, 'x-workspace-id': '5e60ba90-4b3e-4b1a-9dcb-9d76b1a1e3a1' })
    expect(await server().getUser()).toBeNull()
    expect(requested).not.toContain('/duar/authz/resolve')
  })

  it.each([
    ['the authz token is bound to another IdP user', () => [idpToken(), authzToken({ idp_sub: 'google|2' })]],
    ['the authz token was minted for another service', () => [idpToken(), authzToken({ svc: 'other-app' })]],
    ['the authz token has no svc', () => [idpToken(), authzToken({ svc: undefined })]],
    ['neither token names the user', () => [idpToken({ sub: undefined }), authzToken({ idp_sub: undefined })]],
    ['the IdP token was issued to another client', () => [idpToken({ aud: 'other-client' }), authzToken()]],
    ['the IdP token comes from another issuer', () => [idpToken({ iss: 'https://evil.example' }), authzToken()]],
    ['the authz token comes from another issuer', () => [idpToken(), authzToken({ iss: 'https://evil.example' })]],
  ])('returns null, ignoring forged x-duar-* headers, when %s', async (_, tokens) => {
    const [idp, authz] = await Promise.all(tokens())
    request({ authorization: `Bearer ${idp}`, 'x-authz-token': authz, ...FORGED })
    expect(await server().getUser()).toBeNull()
  })

  it('accepts the realm scope as svc', async () => {
    request({ authorization: `Bearer ${await idpToken()}`, 'x-authz-token': await authzToken({ svc: 'realm-x' }) })
    expect((await server({ effectiveScope: 'realm-x' }).getUser())?.userId).toBe('u1')
  })

  it('honors an issuer override for the authz token', async () => {
    request({ authorization: `Bearer ${await idpToken()}`, 'x-authz-token': await authzToken({ iss: 'https://duar.example.com' }) })
    expect((await server({ issuer: 'https://duar.example.com' }).getUser())?.userId).toBe('u1')
  })

  it('defers a missing idpAudience to the first call, then throws (any IdP client would pass)', async () => {
    const s = server({ idpAudience: undefined }) // env var unset at build time
    request({})
    await expect(s.getUser()).rejects.toThrow(/idpAudience/)
  })
})
