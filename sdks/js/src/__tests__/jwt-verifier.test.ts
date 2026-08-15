import { describe, it, expect, vi, beforeEach } from 'vitest'

// We need to mock jose since we can't spin up a real JWKS server
vi.mock('jose', () => ({
  createRemoteJWKSet: vi.fn(() => vi.fn()),
  jwtVerify: vi.fn(),
}))

import { verifyToken, payloadToUser } from '../jwt-verifier'
import { jwtVerify } from 'jose'

const mockPayload = {
  sub: 'user-1',
  email: 'user@test.com',
  name: 'Test User',
  wid: 'ws-1',
  wslug: 'test-ws',
  wrole: 'admin',
  groups: ['g1'],
  aud: 'duar:access',
  iss: 'duar',
  exp: Math.floor(Date.now() / 1000) + 3600,
  iat: Math.floor(Date.now() / 1000),
  jti: 'jti-1',
}

describe('verifyToken', () => {
  beforeEach(() => {
    vi.mocked(jwtVerify).mockResolvedValue({
      payload: mockPayload as any,
      protectedHeader: { alg: 'RS256' },
      key: {} as any,
    } as any)
  })

  it('returns decoded payload on success', async () => {
    const result = await verifyToken('fake-token', {
      jwksUrl: 'http://localhost:9003/.well-known/jwks.json',
    })
    expect(result.sub).toBe('user-1')
    expect(result.email).toBe('user@test.com')
    expect(result.wrole).toBe('admin')
  })

  it('passes audience to jwtVerify', async () => {
    await verifyToken('fake-token', {
      jwksUrl: 'http://localhost:9003/.well-known/jwks.json',
      audience: 'custom:aud',
    })
    expect(jwtVerify).toHaveBeenCalledWith(
      'fake-token',
      expect.anything(),
      expect.objectContaining({ audience: 'custom:aud' }),
    )
  })

  it('defaults audience to duar:access', async () => {
    await verifyToken('fake-token', {
      jwksUrl: 'http://localhost:9003/.well-known/jwks.json',
    })
    expect(jwtVerify).toHaveBeenCalledWith(
      'fake-token',
      expect.anything(),
      expect.objectContaining({ audience: 'duar:access' }),
    )
  })
})

describe('payloadToUser', () => {
  it('maps payload to DuarUser', () => {
    const user = payloadToUser(mockPayload as any)
    expect(user).toEqual({
      userId: 'user-1',
      email: 'user@test.com',
      name: 'Test User',
      workspaceId: 'ws-1',
      workspaceSlug: 'test-ws',
      workspaceRole: 'admin',
      groups: ['g1'],
    })
  })
})
