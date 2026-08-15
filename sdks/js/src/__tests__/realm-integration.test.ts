// sdks/js/src/__tests__/realm-integration.test.ts
// Cross-SDK: the SAME bytes a Python Duar minted, verified by REAL jose (no mock).
// jose's createRemoteJWKSet fetches the JWKS URL — in the Node runtime jose uses
// node:http (not globalThis.fetch), so we stub only the key *transport* by replacing
// createRemoteJWKSet with one that returns createLocalJWKSet over the fixture JWKS.
// jwtVerify and all RS256 crypto are fully real and un-mocked.
import { describe, it, expect, vi } from 'vitest'
import fixturesRaw from '../../../../service/tests/integration/fixtures/fixtures.json'

// ponytail: partial mock — only createRemoteJWKSet is stubbed; jwtVerify stays real
vi.mock('jose', async (importActual) => {
  const actual = await importActual<typeof import('jose')>()
  const jwks = (fixturesRaw as any).jwks
  return {
    ...actual,
    createRemoteJWKSet: () => actual.createLocalJWKSet(jwks),
  }
})

import { verifyM2mToken } from '../m2m'

const fixtures = fixturesRaw as {
  jwks: { keys: object[] }
  tokens: {
    m2m_valid: string
    m2m_expired: string
    m2m_wrong_realm: string
    m2m_aud_target: string
    authz_valid: string
  }
}

const JWKS_URL = 'http://duar-internal:9010/.well-known/jwks.json'

describe('cross-SDK m2m (real jose)', () => {
  it('accepts a real Duar-minted m2m token', async () => {
    const sys = await verifyM2mToken(fixtures.tokens.m2m_valid, {
      jwksUrl: JWKS_URL,
      effectiveScope: 'acme-suite',
      serviceName: 'reports',
    })
    expect(sys.caller).toBe('app-a')
    expect(sys.svc).toBe('acme-suite')
    expect(sys.can('anything')).toBe(true)
  })

  it('rejects a token minted for another realm', async () => {
    await expect(
      verifyM2mToken(fixtures.tokens.m2m_wrong_realm, {
        jwksUrl: JWKS_URL,
        effectiveScope: 'acme-suite',
      }),
    ).rejects.toThrow(/realm/i)
  })

  it('rejects an expired token', async () => {
    await expect(
      verifyM2mToken(fixtures.tokens.m2m_expired, {
        jwksUrl: JWKS_URL,
        effectiveScope: 'acme-suite',
      }),
    ).rejects.toThrow(/exp/i)
  })

  it('rejects a real authz token (wrong audience) through the m2m verifier', async () => {
    await expect(
      verifyM2mToken(fixtures.tokens.authz_valid, {
        jwksUrl: JWKS_URL,
        effectiveScope: 'acme-suite',
      }),
    ).rejects.toThrow(/aud/i)
  })

  it('rejects a token targeted at a different service', async () => {
    await expect(
      verifyM2mToken(fixtures.tokens.m2m_aud_target, {
        jwksUrl: JWKS_URL,
        effectiveScope: 'acme-suite',
        serviceName: 'reports',
      }),
    ).rejects.toThrow(/target/i)
  })
})
