import { describe, it, expect } from 'vitest'
import { issuerFromJwksUrl } from '../issuer'

describe('issuerFromJwksUrl', () => {
  it('keeps a path prefix (Duar BASE_URL under a subpath)', () => {
    expect(issuerFromJwksUrl('https://host/duar/.well-known/jwks.json')).toBe('https://host/duar')
  })
  it('is the origin for a root deployment', () => {
    expect(issuerFromJwksUrl('http://localhost:9003/.well-known/jwks.json')).toBe('http://localhost:9003')
  })
  it('falls back to the origin for a custom JWKS path', () => {
    expect(issuerFromJwksUrl('https://host/keys.json')).toBe('https://host')
  })
})
