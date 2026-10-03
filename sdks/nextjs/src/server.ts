import { cookies, headers } from 'next/headers'
import { authzTokenToUser, type DuarUser, type WorkspaceRole } from '@duar-auth/js'
import { decodeHeaderValue } from './header-codec'
import { createAccessVerifier, type DuarMiddlewareConfig } from './middleware'
import { createAuthzVerifier, type DuarAuthzMiddlewareConfig } from './authz-middleware'

function bearer(h: Headers): string | null {
  const auth = h.get('authorization')
  return auth?.startsWith('Bearer ') ? auth.slice(7) : null
}

/** requireUser / withAuth on top of a verifying getUser. */
function helpers(getUser: () => Promise<DuarUser | null>) {
  /** The verified user; throws `Error('Unauthorized')` without one. */
  async function requireUser(): Promise<DuarUser> {
    const user = await getUser()
    if (!user) {
      throw new Error('Unauthorized')
    }
    return user
  }

  /** Route Handler wrapper: answers 401 JSON (like the middleware) without a verified user. */
  function withAuth<T>(
    handler: (req: Request, user: DuarUser) => Promise<T>,
  ): (req: Request) => Promise<T | Response> {
    return async (req: Request) => {
      const user = await getUser()
      return user ? handler(req, user) : Response.json({ detail: 'Unauthorized' }, { status: 401 })
    }
  }

  return { getUser, requireUser, withAuth }
}

// The factories build their verifier on first use, not at import: `next build` imports route
// modules, and the config's env vars may only exist at runtime. A bad config still throws.

/**
 * Server helpers for `createDuarMiddleware` that verify the access token themselves (the
 * Bearer header, else the `duar_access_token` cookie) instead of trusting the x-duar-*
 * headers, so a skipped middleware can't hand them a forged user. Pass the middleware's config.
 */
export function createDuarServer(config: DuarMiddlewareConfig) {
  let verifier: ReturnType<typeof createAccessVerifier> | undefined
  return helpers(async () => {
    const verify = (verifier ??= createAccessVerifier(config))
    const token = bearer(await headers()) ?? (await cookies()).get('duar_access_token')?.value
    if (!token) return null
    try {
      return await verify(token)
    } catch {
      return null
    }
  })
}

/**
 * Server helpers for `createDuarAuthzMiddleware` that verify the IdP token and X-Authz-Token
 * themselves, with the middleware's exact checks. They never mint: on the auto-resolve path
 * the middleware forwards the minted token as x-authz-token, so without it they return null.
 */
export function createDuarAuthzServer(config: DuarAuthzMiddlewareConfig) {
  let verifier: ReturnType<typeof createAuthzVerifier> | undefined
  return helpers(async () => {
    const verify = (verifier ??= createAuthzVerifier(config))
    const h = await headers()
    const idpToken = bearer(h)
    const authzToken = h.get('x-authz-token')
    if (!idpToken || !authzToken) return null
    try {
      const [idp, authz] = await Promise.all([verify.idp(idpToken), verify.authz(authzToken)])
      if (!verify.bound(idp, authz as unknown as Record<string, unknown>)) return null
      return authzTokenToUser(authzToken, { email: String(idp.email ?? ''), name: String(idp.name ?? '') })
    } catch {
      return null
    }
  })
}

/**
 * Read the current Duar user from request headers (set by middleware).
 * Returns null if the middleware did not set user headers.
 * @deprecated Trusts the x-duar-* headers, which are client-controlled wherever the middleware
 * did not run. Use `createDuarServer` / `createDuarAuthzServer`, which verify the token.
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
    actions: h.get('x-duar-actions')?.split(',').filter(Boolean),
    orgId: h.get('x-duar-org-id'),
    orgSlug: h.get('x-duar-org-slug'),
    orgIsPublic: h.get('x-duar-org-public') === 'true',
  }
}

/**
 * Require an authenticated Duar user. Throws `Error('Unauthorized')` if not found.
 * @deprecated Header-trusting, like `getUser`. Use `createDuarServer` / `createDuarAuthzServer`.
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
  return bearer(await headers())
}

/**
 * HOC for Route Handlers that require authentication.
 * Extracts user from headers and passes to handler.
 * @deprecated Header-trusting, like `getUser`. Use `createDuarServer` / `createDuarAuthzServer`.
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
