import type { TokenStore } from './types'

const PREFIX = 'duar_'

/** Token storage using browser localStorage. */
export class LocalStorageStore implements TokenStore {
  getAccessToken(): string | null {
    return localStorage.getItem(`${PREFIX}access_token`)
  }

  getRefreshToken(): string | null {
    return localStorage.getItem(`${PREFIX}refresh_token`)
  }

  setTokens(access: string, refresh: string): void {
    localStorage.setItem(`${PREFIX}access_token`, access)
    localStorage.setItem(`${PREFIX}refresh_token`, refresh)
  }

  clear(): void {
    localStorage.removeItem(`${PREFIX}access_token`)
    localStorage.removeItem(`${PREFIX}refresh_token`)
  }
}

/**
 * Token storage using browser sessionStorage.
 * Tokens are cleared when the tab is closed, limiting exposure from XSS
 * compared to localStorage (which persists across sessions).
 */
export class SessionStorageStore implements TokenStore {
  getAccessToken(): string | null {
    return sessionStorage.getItem(`${PREFIX}access_token`)
  }

  getRefreshToken(): string | null {
    return sessionStorage.getItem(`${PREFIX}refresh_token`)
  }

  setTokens(access: string, refresh: string): void {
    sessionStorage.setItem(`${PREFIX}access_token`, access)
    sessionStorage.setItem(`${PREFIX}refresh_token`, refresh)
  }

  clear(): void {
    sessionStorage.removeItem(`${PREFIX}access_token`)
    sessionStorage.removeItem(`${PREFIX}refresh_token`)
  }
}

/** In-memory token storage for SSR or testing. */
export class MemoryStore implements TokenStore {
  private accessToken: string | null = null
  private refreshToken: string | null = null

  getAccessToken(): string | null {
    return this.accessToken
  }

  getRefreshToken(): string | null {
    return this.refreshToken
  }

  setTokens(access: string, refresh: string): void {
    this.accessToken = access
    this.refreshToken = refresh
  }

  clear(): void {
    this.accessToken = null
    this.refreshToken = null
  }
}
