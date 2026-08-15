import { headers } from 'next/headers'
import type { DuarUser, WorkspaceRole } from '@duar-auth/js'
import { decodeHeaderValue } from './header-codec'

/**
 * Read the current Duar user from request headers (set by middleware).
 * Returns null if the middleware did not set user headers.
 */
export async function getUser(): Promise<DuarUser | null> {
  const h = await headers()
  const userId = h.get('x-duar-user-id')
  const workspaceId = h.get('x-duar-workspace-id')
  if (!userId || !workspaceId) return null

  return {
    userId,
    email: decodeHeaderValue(h.get('x-duar-email') ?? ''),
    name: decodeHeaderValue(h.get('x-duar-name') ?? ''),
    workspaceId,
    workspaceSlug: h.get('x-duar-workspace-slug') ?? '',
    workspaceRole: (h.get('x-duar-workspace-role') ?? 'viewer') as WorkspaceRole,
    groups: [],
  }
}

/**
 * Require an authenticated Duar user. Throws a 401 error if not found.
 */
export async function requireUser(): Promise<DuarUser> {
  const user = await getUser()
  if (!user) {
    throw new Error('Unauthorized')
  }
  return user
}

/**
 * Get the raw JWT token from the Authorization header.
 */
export async function getToken(): Promise<string | null> {
  const h = await headers()
  const auth = h.get('authorization')
  if (!auth?.startsWith('Bearer ')) return null
  return auth.slice(7)
}

/**
 * HOC for Route Handlers that require authentication.
 * Extracts user from headers and passes to handler.
 */
export function withAuth<T>(
  handler: (req: Request, user: DuarUser) => Promise<T>,
): (req: Request) => Promise<T> {
  return async (req: Request) => {
    const user = await requireUser()
    return handler(req, user)
  }
}

// Realm no-user (m2m) — server only. Mint for outbound system calls, verify inbound.
export { verifyM2mToken, fetchWhoami, M2mTokenClient } from '@duar-auth/js/server'
export type { SystemAuth, WhoamiResponse, M2mVerifyOptions } from '@duar-auth/js/server'
