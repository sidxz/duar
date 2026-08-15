import { type NextRequest, NextResponse } from 'next/server'
import { verifyToken, payloadToUser } from '@duar-auth/js/server'
import { encodeHeaderValue } from './header-codec'

export interface DuarMiddlewareConfig {
  /** URL to the JWKS endpoint. */
  jwksUrl: string
  /** Paths that skip auth (e.g. ["/login", "/auth/callback"]). */
  publicPaths?: string[]
  /** Redirect target for unauthenticated page requests. Defaults to "/login". */
  loginPath?: string
  /** Expected audience. Defaults to "duar:access". */
  audience?: string
  /** Expected JWT issuer claim. Defaults to the origin of jwksUrl. */
  issuer?: string
  /** Optional workspace ID allowlist. */
  allowedWorkspaces?: string[]
}

/**
 * Create a Next.js Edge Middleware that verifies Duar JWTs.
 *
 * Usage in `middleware.ts`:
 * ```ts
 * import { createDuarMiddleware } from '@duar-auth/nextjs/middleware'
 * export default createDuarMiddleware({
 *   jwksUrl: 'http://localhost:9003/.well-known/jwks.json',
 *   publicPaths: ['/login', '/auth/callback'],
 * })
 * export const config = { matcher: ['/((?!_next|favicon.ico).*)'] }
 * ```
 */
export function createDuarMiddleware(config: DuarMiddlewareConfig) {
  const {
    jwksUrl,
    publicPaths = [],
    loginPath = '/login',
    audience = 'duar:access',
    allowedWorkspaces,
  } = config
  const issuer = config.issuer ?? new URL(jwksUrl).origin

  // Warn if JWKS URL is plain HTTP on a non-localhost host
  try {
    const parsed = new URL(jwksUrl)
    const safe = new Set(['localhost', '127.0.0.1', '::1'])
    if (parsed.protocol === 'http:' && !safe.has(parsed.hostname)) {
      console.warn(
        `[duar] (NextMiddleware) Fetching JWKS over plain HTTP from ${parsed.hostname}. ` +
          'Use HTTPS in production to protect token verification.',
      )
    }
  } catch { /* invalid URL — let verifyToken handle it */ }

  const DUAR_HEADERS = [
    'x-duar-user-id',
    'x-duar-email',
    'x-duar-name',
    'x-duar-workspace-id',
    'x-duar-workspace-slug',
    'x-duar-workspace-role',
  ] as const

  return async function middleware(req: NextRequest): Promise<NextResponse> {
    const { pathname } = req.nextUrl

    // Strip any client-sent x-duar-* headers to prevent spoofing.
    // This runs on ALL paths (public and protected) so that downstream
    // server components / route handlers can never see forged identity.
    const requestHeaders = new Headers(req.headers)
    for (const h of DUAR_HEADERS) {
      requestHeaders.delete(h)
    }

    // Skip public paths
    if (publicPaths.some((p) => pathname === p || pathname.startsWith(p + '/'))) {
      return NextResponse.next({ request: { headers: requestHeaders } })
    }

    // Extract token from Authorization header or cookie
    const authHeader = req.headers.get('authorization')
    const token = authHeader?.startsWith('Bearer ')
      ? authHeader.slice(7)
      : req.cookies.get('duar_access_token')?.value

    if (!token) {
      return handleUnauthenticated(req, loginPath)
    }

    try {
      const payload = await verifyToken(token, { jwksUrl, audience, issuer })
      const user = payloadToUser(payload)

      // Check workspace allowlist
      if (allowedWorkspaces && !allowedWorkspaces.includes(user.workspaceId)) {
        return handleUnauthenticated(req, loginPath)
      }

      // Forward verified user info in request headers for server components/route handlers
      requestHeaders.set('x-duar-user-id', user.userId)
      // Email/name may contain code points >255 (ByteString limit) — encode.
      requestHeaders.set('x-duar-email', encodeHeaderValue(user.email))
      requestHeaders.set('x-duar-name', encodeHeaderValue(user.name))
      requestHeaders.set('x-duar-workspace-id', user.workspaceId)
      requestHeaders.set('x-duar-workspace-slug', user.workspaceSlug)
      requestHeaders.set('x-duar-workspace-role', user.workspaceRole)
      return NextResponse.next({ request: { headers: requestHeaders } })
    } catch {
      return handleUnauthenticated(req, loginPath)
    }
  }
}

function handleUnauthenticated(
  req: NextRequest,
  loginPath: string,
): NextResponse {
  const isApiRoute = req.nextUrl.pathname.startsWith('/api/')
  if (isApiRoute) {
    return NextResponse.json(
      { detail: 'Unauthorized' },
      { status: 401 },
    )
  }
  const loginUrl = req.nextUrl.clone()
  loginUrl.pathname = loginPath
  return NextResponse.redirect(loginUrl)
}
