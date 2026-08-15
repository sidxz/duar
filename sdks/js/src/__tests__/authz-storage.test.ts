import { describe, it, expect, beforeEach, vi } from 'vitest'
import { AuthzLocalStorageStore, AuthzMemoryStore } from '../authz-storage'

describe('AuthzMemoryStore', () => {
  let store: AuthzMemoryStore

  beforeEach(() => {
    store = new AuthzMemoryStore()
  })

  it('starts empty', () => {
    expect(store.getIdpToken()).toBeNull()
    expect(store.getAuthzToken()).toBeNull()
    expect(store.getProvider()).toBeNull()
    expect(store.getWorkspaceId()).toBeNull()
    expect(store.getUserIdentity()).toBeNull()
  })

  it('stores and retrieves all tokens', () => {
    store.setTokens('idp-jwt', 'authz-jwt', 'google', 'ws-1')
    expect(store.getIdpToken()).toBe('idp-jwt')
    expect(store.getAuthzToken()).toBe('authz-jwt')
    expect(store.getProvider()).toBe('google')
    expect(store.getWorkspaceId()).toBe('ws-1')
  })

  it('stores and retrieves user identity', () => {
    store.setUserIdentity({ email: 'alice@acme.com', name: 'Alice' })
    expect(store.getUserIdentity()).toEqual({ email: 'alice@acme.com', name: 'Alice' })
  })

  it('clear removes all tokens and identity', () => {
    store.setTokens('idp-jwt', 'authz-jwt', 'google', 'ws-1')
    store.setUserIdentity({ email: 'alice@acme.com', name: 'Alice' })
    store.clear()
    expect(store.getIdpToken()).toBeNull()
    expect(store.getAuthzToken()).toBeNull()
    expect(store.getProvider()).toBeNull()
    expect(store.getWorkspaceId()).toBeNull()
    expect(store.getUserIdentity()).toBeNull()
  })
})

describe('AuthzLocalStorageStore', () => {
  let store: AuthzLocalStorageStore
  let mockStorage: Record<string, string>

  beforeEach(() => {
    mockStorage = {}
    const storageMock = {
      getItem: vi.fn((key: string) => mockStorage[key] ?? null),
      setItem: vi.fn((key: string, value: string) => { mockStorage[key] = value }),
      removeItem: vi.fn((key: string) => { delete mockStorage[key] }),
    }
    vi.stubGlobal('localStorage', storageMock)
    store = new AuthzLocalStorageStore()
  })

  it('stores and retrieves from localStorage (idp token in memory only)', () => {
    store.setTokens('idp-jwt', 'authz-jwt', 'google', 'ws-1')
    // IdP token is kept in memory only — not persisted to reduce XSS blast radius.
    expect(store.getIdpToken()).toBe('idp-jwt')
    expect(store.getAuthzToken()).toBe('authz-jwt')
    expect(store.getProvider()).toBe('google')
    expect(store.getWorkspaceId()).toBe('ws-1')
    expect(localStorage.getItem('duar_idp_token')).toBeNull()
    expect(localStorage.getItem('duar_authz_token')).toBe('authz-jwt')
  })

  it('loses IdP token when a fresh store reads from storage (simulated reload)', () => {
    store.setTokens('idp-jwt', 'authz-jwt', 'google', 'ws-1')
    // New instance = fresh memory; authz token survives via localStorage, IdP token does not.
    const reloaded = new AuthzLocalStorageStore()
    expect(reloaded.getAuthzToken()).toBe('authz-jwt')
    expect(reloaded.getIdpToken()).toBeNull()
  })

  it('stores and retrieves user identity', () => {
    store.setUserIdentity({ email: 'alice@acme.com', name: 'Alice' })
    expect(store.getUserIdentity()).toEqual({ email: 'alice@acme.com', name: 'Alice' })
    expect(localStorage.setItem).toHaveBeenCalledWith('duar_user_email', 'alice@acme.com')
    expect(localStorage.setItem).toHaveBeenCalledWith('duar_user_name', 'Alice')
  })

  it('getUserIdentity returns null when not set', () => {
    expect(store.getUserIdentity()).toBeNull()
  })

  it('clear removes from localStorage', () => {
    store.setTokens('idp-jwt', 'authz-jwt', 'google', 'ws-1')
    store.setUserIdentity({ email: 'alice@acme.com', name: 'Alice' })
    store.clear()
    expect(localStorage.getItem('duar_idp_token')).toBeNull()
    expect(localStorage.getItem('duar_authz_token')).toBeNull()
    expect(localStorage.getItem('duar_user_email')).toBeNull()
    expect(localStorage.getItem('duar_user_name')).toBeNull()
  })

  it('uses duar_ prefix in localStorage keys (authz token + metadata only)', () => {
    store.setTokens('idp-jwt', 'authz-jwt', 'google', 'ws-1')
    // Intentionally does NOT persist the IdP token — that stays in instance memory.
    expect(localStorage.setItem).not.toHaveBeenCalledWith('duar_idp_token', 'idp-jwt')
    expect(localStorage.setItem).toHaveBeenCalledWith('duar_authz_token', 'authz-jwt')
    expect(localStorage.setItem).toHaveBeenCalledWith('duar_idp_provider', 'google')
    expect(localStorage.setItem).toHaveBeenCalledWith('duar_workspace_id', 'ws-1')
  })
})
