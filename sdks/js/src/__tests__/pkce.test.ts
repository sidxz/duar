import { describe, it, expect } from 'vitest'
import { generateCodeVerifier, deriveCodeChallenge } from '../pkce'

describe('PKCE', () => {
  it('generates a code verifier of correct length', () => {
    const verifier = generateCodeVerifier()
    // 32 bytes → 43 base64url chars
    expect(verifier.length).toBe(43)
    // Must be base64url characters only
    expect(verifier).toMatch(/^[A-Za-z0-9_-]+$/)
  })

  it('generates unique verifiers', () => {
    const a = generateCodeVerifier()
    const b = generateCodeVerifier()
    expect(a).not.toBe(b)
  })

  it('derives a code challenge from a verifier', async () => {
    const verifier = generateCodeVerifier()
    const challenge = await deriveCodeChallenge(verifier)
    // SHA-256 → 32 bytes → 43 base64url chars
    expect(challenge.length).toBe(43)
    expect(challenge).toMatch(/^[A-Za-z0-9_-]+$/)
  })

  it('derives the same challenge for the same verifier', async () => {
    const verifier = generateCodeVerifier()
    const c1 = await deriveCodeChallenge(verifier)
    const c2 = await deriveCodeChallenge(verifier)
    expect(c1).toBe(c2)
  })

  it('derives different challenges for different verifiers', async () => {
    const v1 = generateCodeVerifier()
    const v2 = generateCodeVerifier()
    const c1 = await deriveCodeChallenge(v1)
    const c2 = await deriveCodeChallenge(v2)
    expect(c1).not.toBe(c2)
  })
})
