import { type NextRequest, NextResponse } from 'next/server'
import { createRemoteJWKSet, jwtVerify, type JWTPayload } from 'jose'
import { verifyToken } from '@duar-auth/js/server'
import { encodeHeaderValue } from './header-codec'

export interface DuarAuthzMiddlewareConfig {
  /** Base URL of the Duar service. Derives /.well-known/jwks.json for authz token verification. */
  duarUrl: string
  /** JWKS URL for IdP token verification (e.g. Google's JWKS endpoint). */
  idpJwksUrl: string
  /**
   * IdP audience — the OAuth client_id(s) this app is registered as with the
   * IdP. REQUIRED: without this check, any valid ID token from any client of
   * the same IdP authenticates (e.g. any Google OAuth app can mint a token
   * that passes signature verification).
   */
  idpAudience: string | string[]
  /** IdP issuer, e.g. "https://accounts.google.com". Optional but strongly recommended. */
  idpIssuer?: string
  /**
   * Service name — the authz token's `svc` claim must equal this. Prevents
   * a token minted for another service from being replayed here.
   */
  serviceName: string
  /**
   * Realm slug (this service's shared scope). When set, the authz token's `svc`
   * may equal either `serviceName` or this. Realm members resolve it once at
   * startup via `fetchWhoami` from `@duar-auth/js/server`. Omit for standalone.
   */
  effectiveScope?: string
  /** Paths that skip auth (e.g. ["/login", "/api/auth"]). */
  publicPaths?: string[]
  /** Redirect target for unauthenticated page requests. Defaults to "/login". */
  loginPath?: string
  /** Expected JWT issuer for the authz token. Defaults to duarUrl (Duar's BASE_URL, path prefix included). */
  issuer?: string
  /**
   * Mint the authz token server-side when a request carries only the IdP token plus
   * `X-Workspace-Id` (scripts, Postman, Swagger). Cached per (idp_sub, workspace) at
   * 80% of the token TTL with single-flight minting. Requires `serviceKey` and
   * `idpProvider`. Default false — existing behaviour unchanged.
   */
  autoResolve?: boolean
  /** Service key for `POST /authz/resolve`. Server-only env (never NEXT_PUBLIC_). Required with autoResolve. */
  serviceKey?: string
  /** Provider Duar validates the IdP token as: 'google' | 'entra_id'. Required with autoResolve. */
  idpProvider?: string
}

// Cache JWKS sets across invocations (Edge runtime module-scoped)
const jwksSets = new Map<string, ReturnType<typeof createRemoteJWKSet>>()

function getJWKS(url: string) {
  let jwks = jwksSets.get(url)
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(url))
    jwksSets.set(url, jwks)
  }
  return jwks
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const RESOLVE_CACHE_MAX = 4096
const MISSING_BOTH_DETAIL =
  'Missing authz token — send X-Authz-Token, or X-Workspace-Id to resolve one server-side'

/** A failed `POST /authz/resolve`; `status` 0 means the request itself failed. */
class ResolveError extends Error {
  constructor(
    readonly status: number,
    readonly retryAfter: string | null,
    readonly detail: string | null = null,
  ) {
    super(`authz resolve failed: ${status}`)
  }
}

/** Duar's JSON `detail` from an error body, if it sent one. */
async function readDetail(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    return typeof body?.detail === 'string' ? body.detail : null
  } catch {
    return null
  }
}

/** Map a Duar mint failure to the app's response (spec §1). */
function resolveErrorResponse(e: ResolveError): NextResponse {
  if (e.status === 400) {
    // Duar refused the IdP token (aud binding, provider config, ...); its reason is the
    // fastest route to the misconfig, so pass it through.
    const detail = e.detail ? `IdP token rejected by Duar: ${e.detail}` : 'IdP token rejected by Duar'
    return NextResponse.json({ detail }, { status: 401 })
  }
  if (e.status === 401) {
    // Duar refused OUR service key — app config, not the caller's token.
    return NextResponse.json({ detail: 'Authorization service rejected the service key' }, { status: 503 })
  }
  if (e.status === 403 || e.status === 409) {
    return NextResponse.json({ detail: 'Not authorized for this workspace' }, { status: 403 })
  }
  if (e.status === 429) {
    const headers: Record<string, string> = e.retryAfter ? { 'Retry-After': e.retryAfter } : {}
    return NextResponse.json({ detail: 'Authorization service rate limit' }, { status: 429, headers })
  }
  return NextResponse.json({ detail: 'Authorization service unavailable' }, { status: 503 })
}

/**
 * Create a Next.js Edge Middleware that validates dual tokens (AuthZ mode).
 *
 * Validates:
 * 1. IdP token (Authorization: Bearer) — signature + audience (+ issuer if provided)
 * 2. Authz token (X-Authz-Token) — signature + audience (duar:authz) + issuer
 * 3. idp_sub binding — authz token's idp_sub must match IdP token's sub
 * 4. svc binding — authz token's svc must equal configured serviceName
 *
 * Usage in `middleware.ts`:
 * ```ts
 * import { createDuarAuthzMiddleware } from '@duar-auth/nextjs/authz-middleware'
 * export default createDuarAuthzMiddleware({
 *   duarUrl: 'http://localhost:9003',
 *   idpJwksUrl: 'https://www.googleapis.com/oauth2/v3/certs',
 *   idpAudience: process.env.GOOGLE_CLIENT_ID!,
 *   idpIssuer: 'https://accounts.google.com',
 *   serviceName: 'my-app',
 *   publicPaths: ['/login', '/auth/callback'],
 *   // Optional: let scripts call the API with only the IdP token + X-Workspace-Id
 *   autoResolve: true, serviceKey: process.env.DUAR_SERVICE_KEY!, idpProvider: 'google',
 * })
 * export const config = { matcher: ['/((?!_next|favicon.ico).*)'] }
 * ```
 */
export function createDuarAuthzMiddleware(config: DuarAuthzMiddlewareConfig) {
  const {
    duarUrl,
    idpJwksUrl,
    idpAudience,
    idpIssuer,
    serviceName,
    effectiveScope,
    publicPaths = [],
    loginPath = '/login',
    autoResolve = false,
    serviceKey,
    idpProvider,
  } = config

  if (!serviceName) {
    throw new Error('createDuarAuthzMiddleware: serviceName is required')
  }
  if (!idpAudience || (Array.isArray(idpAudience) && idpAudience.length === 0)) {
    throw new Error('createDuarAuthzMiddleware: idpAudience is required')
  }
  if (autoResolve && !serviceKey) {
    throw new Error('createDuarAuthzMiddleware: autoResolve requires serviceKey')
  }
  if (autoResolve && !idpProvider) {
    throw new Error('createDuarAuthzMiddleware: autoResolve requires idpProvider')
  }

  const duarBase = duarUrl.replace(/\/+$/, '')
  const duarJwksUrl = `${duarBase}/.well-known/jwks.json`
  const issuer = config.issuer ?? duarBase

  // Auto-resolve state lives in this closure (not module scope) so each middleware
  // instance — and each test — gets its own cache.
  const resolveCache = new Map<string, { token: string; expiresAt: number }>()
  const resolvePending = new Map<string, Promise<string>>()

  async function mint(key: string, idpToken: string, workspaceId: string): Promise<string> {
    let res: Response
    try {
      res = await fetch(`${duarBase}/authz/resolve`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'X-Service-Key': serviceKey! },
        body: JSON.stringify({ idp_token: idpToken, provider: idpProvider, workspace_id: workspaceId }),
      })
    } catch {
      throw new ResolveError(0, null)
    }
    if (!res.ok) {
      throw new ResolveError(res.status, res.headers.get('retry-after'), await readDetail(res))
    }
    let data: { authz_token?: unknown; expires_in?: unknown }
    try {
      data = await res.json()
    } catch {
      throw new ResolveError(0, null) // a 200 that is not JSON (proxy maintenance page, ...)
    }
    const token = data?.authz_token
    if (typeof token !== 'string' || !token) throw new ResolveError(0, null)
    // 80% of the TTL (same rule as M2mTokenClient) so a cached token never goes out seconds
    // before expiry. Only a numeric expires_in counts (0 → nothing cached), matching the Python SDK.
    const ttl = typeof data.expires_in === 'number' ? data.expires_in : 300
    resolveCache.set(key, { token, expiresAt: Date.now() + 0.8 * ttl * 1000 })
    while (resolveCache.size > RESOLVE_CACHE_MAX) {
      const oldest = resolveCache.keys().next().value
      if (oldest === undefined) break
      resolveCache.delete(oldest)
    }
    return token
  }

  /** Cached token for (idp_sub, workspace), else one shared in-flight mint per key. */
  function resolveOnce(key: string, idpToken: string, workspaceId: string): Promise<string> {
    const cached = resolveCache.get(key)
    if (cached && cached.expiresAt > Date.now()) return Promise.resolve(cached.token)
    let pending = resolvePending.get(key)
    if (!pending) {
      pending = mint(key, idpToken, workspaceId).finally(() => resolvePending.delete(key))
      resolvePending.set(key, pending)
    }
    return pending
  }

  return async function middleware(req: NextRequest): Promise<NextResponse> {
    const { pathname } = req.nextUrl

    // Strip every client-sent x-duar-* header (by prefix, so the strip can't drift
    // from what we set). This runs on ALL paths (public and protected) so that
    // downstream server components / route handlers can never see forged identity.
    const requestHeaders = new Headers(req.headers)
    for (const h of [...requestHeaders.keys()]) {
      if (h.startsWith('x-duar-')) requestHeaders.delete(h)
    }

    // Skip public paths
    if (publicPaths.some((p) => pathname === p || pathname.startsWith(p + '/'))) {
      return NextResponse.next({ request: { headers: requestHeaders } })
    }

    // Extract IdP token from Authorization header
    const authHeader = req.headers.get('authorization')
    const idpToken = authHeader?.startsWith('Bearer ')
      ? authHeader.slice(7)
      : null

    // Extract authz token from X-Authz-Token header. Without autoResolve its absence
    // is a hard 401 (unchanged).
    let authzToken = req.headers.get('x-authz-token')

    if (!idpToken || (!authzToken && !autoResolve)) {
      return handleUnauthenticated(req, loginPath)
    }

    // A caller sending a Bearer token but no authz token is an API client by construction
    // (browsers never attach Bearer on navigation), so auto-path failures are always JSON.
    const autoPath = !authzToken
    const unauthorized = (detail = 'Unauthorized') =>
      autoPath ? NextResponse.json({ detail }, { status: 401 }) : handleUnauthenticated(req, loginPath)

    try {
      const idpVerifyOptions: {
        audience: string | string[]
        issuer?: string
      } = { audience: idpAudience }
      if (idpIssuer) idpVerifyOptions.issuer = idpIssuer

      let idpPayload: JWTPayload
      let authzPayload: Awaited<ReturnType<typeof verifyToken>>

      if (authzToken) {
        // Verify both tokens in parallel.
        // IdP token: signature + audience (+ optional issuer).
        // Authz token: signature + audience via Duar's verifyToken.
        const [idpResult, verified] = await Promise.all([
          jwtVerify(idpToken, getJWKS(idpJwksUrl), idpVerifyOptions),
          verifyToken(authzToken, { jwksUrl: duarJwksUrl, audience: 'duar:authz', issuer }),
        ])
        idpPayload = idpResult.payload
        authzPayload = verified
      } else {
        // Auto-resolve: verify the IdP token FIRST so junk never reaches Duar's rate
        // bucket, then mint (or reuse) an authz token for X-Workspace-Id.
        idpPayload = (await jwtVerify(idpToken, getJWKS(idpJwksUrl), idpVerifyOptions)).payload
        if (!idpPayload.sub) return unauthorized() // never build the cache key from a missing identity
        const workspaceId = req.headers.get('x-workspace-id')
        if (!workspaceId) {
          return unauthorized(MISSING_BOTH_DETAIL)
        }
        if (!UUID_RE.test(workspaceId)) {
          return NextResponse.json({ detail: 'Invalid X-Workspace-Id' }, { status: 400 })
        }
        const key = `${idpPayload.sub}|${workspaceId.toLowerCase()}`
        try {
          authzToken = await resolveOnce(key, idpToken, workspaceId)
        } catch (e) {
          if (e instanceof ResolveError) return resolveErrorResponse(e)
          throw e
        }
        try {
          authzPayload = await verifyToken(authzToken, { jwksUrl: duarJwksUrl, audience: 'duar:authz', issuer })
        } catch {
          resolveCache.delete(key) // a minted token we cannot verify must not be served from cache again
          return unauthorized('Invalid authz token')
        }
        // Server code that reads the raw header keeps working on the auto path.
        requestHeaders.set('x-authz-token', authzToken)
      }

      // Check idp_sub binding: authz token's idp_sub must match IdP token's sub.
      const authzClaims = authzPayload as unknown as Record<string, unknown>
      if (!idpPayload.sub || !authzClaims.idp_sub || authzClaims.idp_sub !== idpPayload.sub) {
        return unauthorized()
      }

      // Enforce svc binding: the authz token was minted for this service's shared
      // scope — its own name (standalone) or its realm slug (effectiveScope).
      const allowedSvc = new Set([serviceName, effectiveScope].filter(Boolean))
      if (!authzClaims.svc || !allowedSvc.has(authzClaims.svc as string)) {
        return unauthorized()
      }

      // Forward verified user info in request headers for server components / route handlers
      // Identity (email, name) comes from IdP token; authorization from authz token
      requestHeaders.set('x-duar-user-id', String(authzPayload.sub))
      // Email/name may contain code points >255 (ByteString limit) — encode.
      requestHeaders.set(
        'x-duar-email',
        encodeHeaderValue(String(idpPayload.email ?? '')),
      )
      requestHeaders.set(
        'x-duar-name',
        encodeHeaderValue(String(idpPayload.name ?? '')),
      )
      requestHeaders.set('x-duar-workspace-id', String(authzPayload.wid))
      requestHeaders.set('x-duar-workspace-slug', String(authzPayload.wslug))
      requestHeaders.set('x-duar-workspace-role', String(authzPayload.wrole))
      requestHeaders.set('x-duar-idp-sub', String(authzClaims.idp_sub))
      // Action names match ^[a-z][a-z0-9_.:-]*$ — no commas, header-safe.
      requestHeaders.set('x-duar-actions', ((authzClaims.actions as string[] | undefined) ?? []).join(','))
      // Org slugs match ^[a-z0-9][a-z0-9-]*[a-z0-9]$ — header-safe, no encoding needed.
      if (authzClaims.oid) requestHeaders.set('x-duar-org-id', String(authzClaims.oid))
      if (authzClaims.oslug) requestHeaders.set('x-duar-org-slug', String(authzClaims.oslug))
      requestHeaders.set('x-duar-org-public', String(authzClaims.opub === true))

      return NextResponse.next({ request: { headers: requestHeaders } })
    } catch {
      return unauthorized()
    }
  }
}

function handleUnauthenticated(
  req: NextRequest,
  loginPath: string,
): NextResponse {
  const isApiRoute = req.nextUrl.pathname.startsWith('/api/')
  if (isApiRoute) {
    return NextResponse.json({ detail: 'Unauthorized' }, { status: 401 })
  }
  const loginUrl = req.nextUrl.clone()
  loginUrl.pathname = loginPath
  return NextResponse.redirect(loginUrl)
}
