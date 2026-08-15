import type { AuthzTokenStore, UserIdentity } from './authz-types'

const PREFIX = 'duar_'

/**
 * Authz token storage using browser localStorage — the authz token plus
 * session metadata are persisted; the IdP token is kept in memory only and
 * does NOT survive a page reload.
 *
 * Rationale: the IdP token is long-lived (Google ID tokens last ~1 hour) and
 * trust-critical — an XSS reading it can impersonate the user across every
 * service the user belongs to, for the token's full lifetime. Persisting it
 * to localStorage dramatically widens the blast radius of XSS. Keeping it in
 * memory only means XSS still compromises the current tab, but a page reload
 * (or new tab) forces a fresh OAuth round-trip rather than silently reusing
 * a stolen token.
 *
 * Trade-off: on reload the authz token exists in localStorage but no IdP
 * token is available, so the SDK treats the session as requiring re-auth.
 * Apps that need persistent sessions should implement a server-backed
 * ``CookieStore`` (HttpOnly cookie, token held server-side) instead.
 */
export class AuthzLocalStorageStore implements AuthzTokenStore {
  private idpToken: string | null = null

  getIdpToken(): string | null {
    return this.idpToken
  }

  getAuthzToken(): string | null {
    return localStorage.getItem(`${PREFIX}authz_token`)
  }

  getProvider(): string | null {
    return localStorage.getItem(`${PREFIX}idp_provider`)
  }

  getWorkspaceId(): string | null {
    return localStorage.getItem(`${PREFIX}workspace_id`)
  }

  getUserIdentity(): UserIdentity | null {
    const email = localStorage.getItem(`${PREFIX}user_email`)
    const name = localStorage.getItem(`${PREFIX}user_name`)
    if (email == null || name == null) return null
    return { email, name }
  }

  setTokens(idpToken: string, authzToken: string, provider: string, workspaceId: string): void {
    this.idpToken = idpToken
    localStorage.setItem(`${PREFIX}authz_token`, authzToken)
    localStorage.setItem(`${PREFIX}idp_provider`, provider)
    localStorage.setItem(`${PREFIX}workspace_id`, workspaceId)
  }

  setUserIdentity(identity: UserIdentity): void {
    localStorage.setItem(`${PREFIX}user_email`, identity.email)
    localStorage.setItem(`${PREFIX}user_name`, identity.name)
  }

  clear(): void {
    this.idpToken = null
    localStorage.removeItem(`${PREFIX}authz_token`)
    localStorage.removeItem(`${PREFIX}idp_provider`)
    localStorage.removeItem(`${PREFIX}workspace_id`)
    localStorage.removeItem(`${PREFIX}user_email`)
    localStorage.removeItem(`${PREFIX}user_name`)
    // Clear legacy idp_token key from older SDK versions that persisted it
    localStorage.removeItem(`${PREFIX}idp_token`)
  }
}

/** In-memory authz token storage for SSR or testing. */
export class AuthzMemoryStore implements AuthzTokenStore {
  private idpToken: string | null = null
  private authzToken: string | null = null
  private provider: string | null = null
  private workspaceId: string | null = null
  private identity: UserIdentity | null = null

  getIdpToken(): string | null {
    return this.idpToken
  }

  getAuthzToken(): string | null {
    return this.authzToken
  }

  getProvider(): string | null {
    return this.provider
  }

  getWorkspaceId(): string | null {
    return this.workspaceId
  }

  getUserIdentity(): UserIdentity | null {
    return this.identity
  }

  setTokens(idpToken: string, authzToken: string, provider: string, workspaceId: string): void {
    this.idpToken = idpToken
    this.authzToken = authzToken
    this.provider = provider
    this.workspaceId = workspaceId
  }

  setUserIdentity(identity: UserIdentity): void {
    this.identity = identity
  }

  clear(): void {
    this.idpToken = null
    this.authzToken = null
    this.provider = null
    this.workspaceId = null
    this.identity = null
  }
}
