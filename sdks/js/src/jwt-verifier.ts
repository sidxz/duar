import { createRemoteJWKSet, jwtVerify } from 'jose'
import type { JWTPayload, DuarUser, VerifyOptions } from './types'

const jwksSets = new Map<string, ReturnType<typeof createRemoteJWKSet>>()

function getJWKS(url: string) {
  let jwks = jwksSets.get(url)
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(url))
    jwksSets.set(url, jwks)
  }
  return jwks
}

/**
 * Verify and decode a Duar JWT using JWKS.
 * Uses `jose` (Edge-compatible, zero native deps).
 */
export async function verifyToken(
  token: string,
  options: VerifyOptions,
): Promise<JWTPayload> {
  const jwks = getJWKS(options.jwksUrl)
  const { payload } = await jwtVerify(token, jwks, {
    audience: options.audience ?? 'duar:access',
    issuer: options.issuer,
  })
  return payload as unknown as JWTPayload
}

/** Map a verified JWT payload to a DuarUser object. */
export function payloadToUser(payload: JWTPayload): DuarUser {
  return {
    userId: payload.sub,
    email: payload.email,
    name: payload.name,
    workspaceId: payload.wid,
    workspaceSlug: payload.wslug,
    workspaceRole: payload.wrole,
    groups: payload.groups ?? [],
  }
}
