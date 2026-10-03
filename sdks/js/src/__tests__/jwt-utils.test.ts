import { describe, it, expect } from 'vitest'
import { parseJwt, isTokenExpired, tokenToUser, authzTokenToUser } from '../jwt-utils'

// Helper to create a fake JWT (no signature verification in browser)
function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' }))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  const body = btoa(JSON.stringify(payload))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `${header}.${body}.fake-signature`
}

const samplePayload = {
  sub: 'user-123',
  email: 'test@example.com',
  name: 'Test User',
  wid: 'ws-456',
  wslug: 'my-workspace',
  wrole: 'editor',
  groups: ['group-a'],
  oid: 'org-1',
  oslug: 'acme',
  opub: false,
  aud: 'duar:access',
  iss: 'duar',
  exp: Math.floor(Date.now() / 1000) + 3600,
  iat: Math.floor(Date.now() / 1000),
  jti: 'token-id-789',
}

describe('parseJwt', () => {
  it('decodes JWT payload correctly', () => {
    const token = makeJwt(samplePayload)
    const parsed = parseJwt(token)
    expect(parsed.sub).toBe('user-123')
    expect(parsed.email).toBe('test@example.com')
    expect(parsed.wid).toBe('ws-456')
    expect(parsed.wrole).toBe('editor')
    expect(parsed.groups).toEqual(['group-a'])
  })

  it('throws on invalid JWT format', () => {
    expect(() => parseJwt('not-a-jwt')).toThrow('Invalid JWT format')
  })
})

describe('isTokenExpired', () => {
  it('returns false for valid token', () => {
    const token = makeJwt({ ...samplePayload, exp: Math.floor(Date.now() / 1000) + 3600 })
    expect(isTokenExpired(token)).toBe(false)
  })

  it('returns true for expired token', () => {
    const token = makeJwt({ ...samplePayload, exp: Math.floor(Date.now() / 1000) - 10 })
    expect(isTokenExpired(token)).toBe(true)
  })

  it('respects buffer seconds', () => {
    const exp = Math.floor(Date.now() / 1000) + 30
    const token = makeJwt({ ...samplePayload, exp })
    expect(isTokenExpired(token, 0)).toBe(false)
    expect(isTokenExpired(token, 60)).toBe(true)
  })

  it('returns true for invalid token', () => {
    expect(isTokenExpired('garbage')).toBe(true)
  })
})

describe('tokenToUser', () => {
  it('maps JWT claims to DuarUser', () => {
    const token = makeJwt(samplePayload)
    const user = tokenToUser(token)
    expect(user).toEqual({
      userId: 'user-123',
      email: 'test@example.com',
      name: 'Test User',
      workspaceId: 'ws-456',
      workspaceSlug: 'my-workspace',
      workspaceRole: 'editor',
      groups: ['group-a'],
      orgId: 'org-1',
      orgSlug: 'acme',
      orgIsPublic: false,
    })
  })

  it('treats only a boolean true opub as public', () => {
    expect(tokenToUser(makeJwt({ ...samplePayload, opub: 'true' })).orgIsPublic).toBe(false)
    expect(tokenToUser(makeJwt({ ...samplePayload, opub: true })).orgIsPublic).toBe(true)
  })
})

const authzPayload = {
  sub: 'user-123',
  idp_sub: 'google|456',
  svc: 'notes',
  wid: 'ws-456',
  wslug: 'my-workspace',
  wrole: 'editor',
  actions: ['notes:create'],
  oid: 'org-public',
  oslug: 'public',
  opub: true,
  aud: 'duar:authz',
  iss: 'duar',
  exp: Math.floor(Date.now() / 1000) + 300,
  iat: Math.floor(Date.now() / 1000),
  jti: 'authz-token-789',
  type: 'authz',
}

describe('authzTokenToUser', () => {
  it('maps authz token claims with identity to DuarUser', () => {
    const token = makeJwt(authzPayload)
    const user = authzTokenToUser(token, { email: 'test@example.com', name: 'Test User' })
    expect(user).toEqual({
      userId: 'user-123',
      email: 'test@example.com',
      name: 'Test User',
      workspaceId: 'ws-456',
      workspaceSlug: 'my-workspace',
      workspaceRole: 'editor',
      groups: [],
      actions: ['notes:create'],
      orgId: 'org-public',
      orgSlug: 'public',
      orgIsPublic: true,
    })
  })

  it('defaults org fields when the claims are absent', () => {
    const { oid: _a, oslug: _b, opub: _c, ...noOrg } = authzPayload
    const user = authzTokenToUser(makeJwt(noOrg), null)
    expect([user.orgId, user.orgSlug, user.orgIsPublic]).toEqual([null, null, false])
  })

  it('defaults actions to [] when the claim is absent', () => {
    const { actions: _, ...noActions } = authzPayload
    const user = authzTokenToUser(makeJwt(noActions), null)
    expect(user.actions).toEqual([])
  })

  it('ignores wrong-typed actions / opub claims', () => {
    // A string actions claim would make includes() a substring match ('admin:all'.includes('admin')).
    const user = authzTokenToUser(makeJwt({ ...authzPayload, actions: 'admin:all', opub: 'true' }), null)
    expect(user.actions).toEqual([])
    expect(user.orgIsPublic).toBe(false)
  })

  it('returns empty strings when identity is null', () => {
    const token = makeJwt(authzPayload)
    const user = authzTokenToUser(token, null)
    expect(user.email).toBe('')
    expect(user.name).toBe('')
    expect(user.userId).toBe('user-123')
    expect(user.workspaceId).toBe('ws-456')
  })

  it('returns empty groups for authz tokens', () => {
    const token = makeJwt(authzPayload)
    const user = authzTokenToUser(token, { email: 'a@b.com', name: 'A' })
    expect(user.groups).toEqual([])
  })
})
