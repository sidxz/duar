// Shared pieces of the JS system tests. The world (workspace, apps, members)
// was built by the Python tests that ran just before; see ../conftest.py.
import { createSign } from 'node:crypto'
import { readFileSync } from 'node:fs'

const work = process.env.SYSTEM_TEST_DIR
export const world = JSON.parse(readFileSync(`${work}/world.json`, 'utf8'))
export const BASE = world.base
export const JWKS_URL = `${BASE}/.well-known/jwks.json`

/**
 * Follow a Duar login URL through Dex as `email`, by hand: redirects one hop at
 * a time, Duar's session cookie carried along, the Dex form posted. Returns the
 * URL the browser is finally sent to once it starts with `until`.
 */
export async function signInThroughDex(startUrl, email, until) {
  const cookies = new Map()
  let url = startUrl
  let init = {}
  for (let hop = 0; hop < 12; hop++) {
    // Dex's issuer is its compose-network name; from the host it is a published port.
    url = url.replace('http://dex:5556', 'http://localhost:15556')
    const toDuar = url.startsWith(BASE)
    const headers = { ...init.headers }
    if (toDuar && cookies.size) headers.cookie = [...cookies].map(([k, v]) => `${k}=${v}`).join('; ')
    const res = await fetch(url, { ...init, headers, redirect: 'manual' })
    if (toDuar) {
      for (const cookie of res.headers.getSetCookie()) {
        const pair = cookie.split(';')[0]
        cookies.set(pair.slice(0, pair.indexOf('=')), pair.slice(pair.indexOf('=') + 1))
      }
    }
    if (res.status >= 300 && res.status < 400) {
      url = new URL(res.headers.get('location'), url).href
      if (url.startsWith(until)) return url
      init = {}
      continue
    }
    const page = await res.text()
    const form = page.match(/<form method="post" action="([^"]+)"/)
    if (!form || init.method === 'POST') {
      throw new Error(`sign-in stopped at ${res.status} ${url}: ${page.slice(0, 200)}`)
    }
    url = new URL(form[1].replaceAll('&amp;', '&'), url).href
    init = {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ login: email, password: 'password' }).toString(),
    }
  }
  throw new Error(`still redirecting after 12 hops, last to ${url}`)
}

/** The two browser globals the SDK's browser clients use besides fetch and crypto. */
export function installBrowserGlobals() {
  const values = new Map()
  const sessionStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, String(value)),
    removeItem: (key) => void values.delete(key),
  }
  const window = { location: { href: '', search: '', hash: '', origin: new URL(world.appCallback).origin } }
  Object.defineProperty(globalThis, 'sessionStorage', { value: sessionStorage, configurable: true })
  Object.defineProperty(globalThis, 'window', { value: window, configurable: true })
  return window
}

/**
 * An ID token as the AuthZ-mode identity provider would issue it. Signed with
 * node:crypto so this project depends on nothing but the SDK packages: any
 * library an SDK needs has to come from that SDK's own dependencies.
 */
export function idpToken(email, claims = {}) {
  const now = Math.floor(Date.now() / 1000)
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString('base64url')
  const body = {
    iss: world.idpIssuer,
    aud: world.idpAudience,
    sub: `idp|${email}`,
    email,
    email_verified: true,
    name: email.split('@')[0],
    iat: now,
    exp: now + 300,
    ...claims,
  }
  const signed = `${encode({ alg: 'RS256', typ: 'JWT' })}.${encode(body)}`
  const key = readFileSync(`${work}/idp-private.pem`, 'utf8')
  return `${signed}.${createSign('RSA-SHA256').update(signed).sign(key, 'base64url')}`
}

/** Flip the signature of a JWT so it no longer verifies. */
export function tamper(token) {
  const [head, payload, signature] = token.split('.')
  const tail = signature.endsWith('AA') ? 'BB' : 'AA'
  return `${head}.${payload}.${signature.slice(0, -2)}${tail}`
}
