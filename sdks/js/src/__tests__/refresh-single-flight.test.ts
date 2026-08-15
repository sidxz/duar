import { describe, it, expect, vi, afterEach } from 'vitest'
import { DuarAuth } from '../client'
import { MemoryStore } from '../storage'
import type { DuarUser } from '../types'

const tick = () => new Promise((r) => setTimeout(r, 0))

function makeJwt(payload: Record<string, unknown>): string {
  const enc = (o: unknown) =>
    btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `${enc({ alg: 'RS256', typ: 'JWT' })}.${enc(payload)}.sig`
}

const now = Math.floor(Date.now() / 1000)
const basePayload = {
  sub: 'u1',
  email: 'u@test.com',
  name: 'U',
  wid: 'ws-1',
  wslug: 'ws',
  wrole: 'editor',
  groups: [],
  aud: 'duar:access',
  iss: 'duar',
  iat: now,
  exp: now + 3600,
}

/** A stand-in for the Web Locks API that grants the lock to one caller at a
 * time (exclusive), serializing callbacks the way real navigator.locks does
 * across tabs of one origin. */
function serializingLocks() {
  let tail: Promise<unknown> = Promise.resolve()
  return {
    request(_name: string, _opts: unknown, cb: () => Promise<unknown>) {
      const result = tail.then(() => cb())
      tail = result.then(
        () => {},
        () => {},
      )
      return result
    },
  }
}

describe('cross-tab refresh single-flight', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('two tabs sharing a store issue only one network refresh, and both pick up the new token', async () => {
    vi.stubGlobal('navigator', { locks: serializingLocks() })

    const oldAccess = makeJwt(basePayload)
    const newAccess = makeJwt({ ...basePayload, jti: 'new' })

    // One store object shared by both clients models a localStorage store seen
    // by two tabs of the same origin.
    const store = new MemoryStore()
    store.setTokens(oldAccess, 'refresh-0')

    let refreshPosts = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (String(url).endsWith('/auth/refresh')) {
          refreshPosts++
          return new Response(
            JSON.stringify({ access_token: newAccess, refresh_token: 'refresh-1' }),
            { status: 200 },
          )
        }
        return new Response('{}', { status: 200 })
      }),
    )

    const cfg = {
      duarUrl: 'http://duar.test',
      clientId: 'app-1',
      storage: store,
      autoRefresh: false,
    }
    const tabA = new DuarAuth(cfg)
    const tabB = new DuarAuth(cfg)

    const [ra, rb] = await Promise.all([tabA.refresh(), tabB.refresh()])

    // Only ONE tab hit the network — the other must have picked up the rotated
    // token instead of replaying the consumed one (which trips reuse detection
    // and logs every tab out).
    expect(refreshPosts).toBe(1)
    expect(ra).toBe(true)
    expect(rb).toBe(true)
    expect(store.getRefreshToken()).toBe('refresh-1')
    expect(tabA.getToken()).toBe(newAccess)
    expect(tabB.getToken()).toBe(newAccess)

    tabA.destroy()
    tabB.destroy()
  })

  it('scheduleRefresh subtracts jitter so tabs do not wake at the same instant', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const setSpy = vi.spyOn(globalThis, 'setTimeout')
    const exp = Math.floor(Date.now() / 1000) + 3600
    const store = new MemoryStore()
    store.setTokens(makeJwt({ ...basePayload, exp }), 'r')

    const client = new DuarAuth({
      duarUrl: 'http://duar.test',
      clientId: 'app-1',
      storage: store,
      autoRefresh: true,
      refreshBuffer: 60,
    })

    const base = exp * 1000 - Date.now() - 60 * 1000
    const delay = setSpy.mock.calls
      .map((c) => c[1])
      .filter((d): d is number => typeof d === 'number' && d > 1000)
      .pop() as number

    // Without jitter delay would equal base exactly; jitter pulls it earlier.
    expect(delay).toBeLessThan(base)
    expect(base - delay).toBeGreaterThan(1000)
    client.destroy()
  })

  it('a tab that times out waiting for the lock does not replay the captured token', async () => {
    vi.useFakeTimers()
    try {
      // A lock that grants once and never releases (the holder's refresh hangs),
      // then rejects later waiters when their AbortSignal fires — the real Web
      // Locks behavior on an acquisition timeout.
      let held = false
      const heldLock = {
        request(_name: string, opts: { signal?: AbortSignal }, cb: () => Promise<unknown>) {
          if (!held) {
            held = true
            return Promise.resolve(cb()) // holder: cb hangs, lock never released
          }
          return new Promise((_res, rej) => {
            const e = new Error('aborted')
            e.name = 'AbortError'
            opts.signal?.addEventListener('abort', () => rej(e))
          })
        },
      }
      vi.stubGlobal('navigator', { locks: heldLock })

      const store = new MemoryStore()
      store.setTokens(makeJwt(basePayload), 'refresh-0')

      let refreshPosts = 0
      vi.stubGlobal(
        'fetch',
        vi.fn((url: string) => {
          if (String(url).endsWith('/auth/refresh')) {
            refreshPosts++
            return new Promise(() => {}) // hang → holder keeps the lock past the timeout
          }
          return Promise.resolve(new Response('{}', { status: 200 }))
        }),
      )

      const cfg = {
        duarUrl: 'http://duar.test',
        clientId: 'app-1',
        storage: store,
        autoRefresh: false,
      }
      const tabA = new DuarAuth(cfg) // grabs + holds the lock (its refresh hangs)
      const tabB = new DuarAuth(cfg)

      void tabA.refresh() // do not await — holds the lock forever
      const bResult = tabB.refresh()
      await vi.advanceTimersByTimeAsync(6000) // fire tabB's lock-acquire timeout

      expect(await bResult).toBe(false) // failed soft
      expect(refreshPosts).toBe(1) // tabB did NOT replay refresh-0 unlocked

      tabA.destroy()
      tabB.destroy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('logout in one tab propagates to other tabs of the same app', async () => {
    const cfg = (store: MemoryStore) => ({
      duarUrl: 'http://duar.test',
      clientId: 'app-1',
      storage: store,
      autoRefresh: false,
    })
    const storeA = new MemoryStore()
    storeA.setTokens(makeJwt(basePayload), 'ra')
    const storeB = new MemoryStore()
    storeB.setTokens(makeJwt(basePayload), 'rb')

    const tabA = new DuarAuth(cfg(storeA))
    const tabB = new DuarAuth(cfg(storeB))

    let bUser: DuarUser | null | 'unset' = 'unset'
    tabB.onAuthStateChange((u) => {
      bUser = u
    })

    tabA.logout()
    await tick() // BroadcastChannel delivers on a later task

    expect(bUser).toBe(null)
    expect(storeB.getAccessToken()).toBeNull()

    tabA.destroy()
    tabB.destroy()
  })
})
