import { describe, it, expect, beforeEach, vi } from 'vitest'
import { LocalStorageStore, MemoryStore } from '../storage'

describe('LocalStorageStore', () => {
  let store: LocalStorageStore
  let mockStorage: Record<string, string>

  beforeEach(() => {
    mockStorage = {}
    const storageMock = {
      getItem: vi.fn((key: string) => mockStorage[key] ?? null),
      setItem: vi.fn((key: string, value: string) => { mockStorage[key] = value }),
      removeItem: vi.fn((key: string) => { delete mockStorage[key] }),
    }
    vi.stubGlobal('localStorage', storageMock)
    store = new LocalStorageStore()
  })

  it('returns null when no tokens', () => {
    expect(store.getAccessToken()).toBeNull()
    expect(store.getRefreshToken()).toBeNull()
  })

  it('stores and retrieves tokens', () => {
    store.setTokens('access123', 'refresh456')
    expect(store.getAccessToken()).toBe('access123')
    expect(store.getRefreshToken()).toBe('refresh456')
  })

  it('clears tokens', () => {
    store.setTokens('a', 'r')
    store.clear()
    expect(store.getAccessToken()).toBeNull()
    expect(store.getRefreshToken()).toBeNull()
  })

  it('uses duar_ prefix in localStorage', () => {
    store.setTokens('a', 'r')
    expect(localStorage.setItem).toHaveBeenCalledWith('duar_access_token', 'a')
    expect(localStorage.setItem).toHaveBeenCalledWith('duar_refresh_token', 'r')
  })
})

describe('MemoryStore', () => {
  let store: MemoryStore

  beforeEach(() => {
    store = new MemoryStore()
  })

  it('returns null when no tokens', () => {
    expect(store.getAccessToken()).toBeNull()
    expect(store.getRefreshToken()).toBeNull()
  })

  it('stores and retrieves tokens', () => {
    store.setTokens('access123', 'refresh456')
    expect(store.getAccessToken()).toBe('access123')
    expect(store.getRefreshToken()).toBe('refresh456')
  })

  it('clears tokens', () => {
    store.setTokens('a', 'r')
    store.clear()
    expect(store.getAccessToken()).toBeNull()
    expect(store.getRefreshToken()).toBeNull()
  })
})
