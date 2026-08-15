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
    })
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
    })
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
