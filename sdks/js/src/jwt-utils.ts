import type { AuthzJWTPayload, JWTPayload, DuarUser } from './types'
import type { UserIdentity } from './authz-types'

/**
 * Decode a JWT payload without verifying the signature.
 * For browser-side use only — server-side code must use `verifyToken` from `@duar-auth/js/server`.
 */
export function parseJwt(token: string): JWTPayload {
  const parts = token.split('.')
  if (parts.length !== 3) throw new Error('Invalid JWT format')

  const payload = parts[1]
  // Base64url → base64, then decode
  const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
  const json = atob(padded)
  return JSON.parse(json) as JWTPayload
}

/** Check if a JWT is expired (with optional buffer in seconds). */
export function isTokenExpired(token: string, bufferSeconds = 0): boolean {
  try {
    const payload = parseJwt(token)
    return Date.now() >= (payload.exp - bufferSeconds) * 1000
  } catch {
    return true
  }
}

/** Map access token JWT claims to a DuarUser object. */
export function tokenToUser(token: string): DuarUser {
  const p = parseJwt(token)
  return {
    userId: p.sub,
    email: p.email,
    name: p.name,
    workspaceId: p.wid,
    workspaceSlug: p.wslug,
    workspaceRole: p.wrole,
    groups: p.groups ?? [],
  }
}

/** Map authz token claims + cached identity to a DuarUser object. */
export function authzTokenToUser(token: string, identity: UserIdentity | null): DuarUser {
  const p = parseJwt(token) as unknown as AuthzJWTPayload
  return {
    userId: p.sub,
    email: identity?.email ?? '',
    name: identity?.name ?? '',
    workspaceId: p.wid,
    workspaceSlug: p.wslug,
    workspaceRole: p.wrole,
    groups: [],
  }
}
