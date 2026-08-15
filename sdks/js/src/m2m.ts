import { createRemoteJWKSet, jwtVerify } from 'jose'
import type { M2mJWTPayload, M2mVerifyOptions, SystemAuth, WhoamiResponse } from './types'

const M2M_AUDIENCE = 'sentinel:m2m'

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
 * Verify an inbound no-user realm token (server entry only — never the browser).
 *
 * Receiver side of Flow B. Trust is rooted in Duar's RS256 signature plus
 * aud/type/svc binding — never app↔app trust. The token's `svc` must equal this
 * service's `effectiveScope`, so a token minted for another realm cannot be
 * replayed here. Throws on any failure.
 */
export async function verifyM2mToken(
  token: string,
  options: M2mVerifyOptions,
): Promise<SystemAuth> {
  const { payload } = await jwtVerify(token, getJWKS(options.jwksUrl), {
    audience: M2M_AUDIENCE,
    issuer: options.issuer,
  })
  const claims = payload as unknown as M2mJWTPayload
  if (claims.type !== 'm2m') {
    throw new Error('Not an m2m token')
  }
  if (claims.svc !== options.effectiveScope) {
    throw new Error('m2m token was issued for a different realm')
  }
  if (
    claims.aud_target != null &&
    options.serviceName !== undefined &&
    claims.aud_target !== options.serviceName
  ) {
    throw new Error('m2m token targets a different service')
  }
  const actions = claims.actions ?? []
  return {
    caller: claims.caller,
    actions,
    svc: claims.svc,
    can: (action: string) => actions.includes('*') || actions.includes(action),
  }
}

/**
 * Self-discover this service's shared scope from Duar (server entry only).
 * Standalone services get `effective_scope === service_name` and `realm: null`.
 */
export async function fetchWhoami(opts: {
  duarUrl: string
  serviceKey: string
}): Promise<WhoamiResponse> {
  const base = opts.duarUrl.replace(/\/+$/, '')
  const res = await fetch(`${base}/realm/whoami`, {
    headers: { 'X-Service-Key': opts.serviceKey },
  })
  if (!res.ok) throw new Error(`whoami failed: ${res.status}`)
  return res.json() as Promise<WhoamiResponse>
}

/**
 * Mints and caches no-user realm m2m tokens for outbound system calls (sender
 * side of Flow B). Server entry only — never construct this in a browser; it
 * holds the service key. Re-mints only once past ~80% of the token's TTL.
 */
export class M2mTokenClient {
  private readonly base: string
  private readonly serviceKey: string
  private token: string | null = null
  private refreshAt = 0
  private mintPromise: Promise<string> | null = null

  constructor(duarUrl: string, serviceKey: string) {
    this.base = duarUrl.replace(/\/+$/, '')
    this.serviceKey = serviceKey
  }

  /** Return a cached token if still fresh, else mint a new one. */
  async getToken(): Promise<string> {
    if (this.token && Date.now() < this.refreshAt) return this.token
    // Share one in-flight mint across concurrent callers (same pattern as
    // DuarAuth.refresh) so N parallel calls don't issue N mint requests.
    if (!this.mintPromise) {
      this.mintPromise = this.mint().finally(() => {
        this.mintPromise = null
      })
    }
    return this.mintPromise
  }

  private async mint(): Promise<string> {
    const res = await fetch(`${this.base}/realm/m2m-token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Service-Key': this.serviceKey },
      body: JSON.stringify({}),
    })
    if (!res.ok) throw new Error(`m2m mint failed: ${res.status}`)
    const data = (await res.json()) as { token: string; expires_in: number }
    this.token = data.token
    this.refreshAt = Date.now() + data.expires_in * 0.8 * 1000
    return this.token
  }
}
