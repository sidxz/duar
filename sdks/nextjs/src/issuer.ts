/**
 * Duar signs `iss` = its BASE_URL, which may carry a path prefix
 * (e.g. https://host/duar). Derive the default from the JWKS URL rather than
 * its origin; fall back to the origin for a JWKS served at a custom path.
 */
export function issuerFromJwksUrl(jwksUrl: string): string {
  const m = /^(.+?)\/+\.well-known\/jwks\.json\/?$/.exec(jwksUrl)
  return m ? m[1] : new URL(jwksUrl).origin
}
