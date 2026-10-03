import { describe, it, expect, vi } from 'vitest'

vi.mock('next/headers', () => ({ headers: vi.fn() }))

import { headers } from 'next/headers'
import { getUser } from '../server'

const BASE = { 'x-duar-user-id': 'u1', 'x-duar-workspace-id': 'ws-1' }
async function userFrom(h: Record<string, string>) {
  vi.mocked(headers).mockResolvedValueOnce(new Headers(h) as any)
  return getUser()
}

describe('getUser', () => {
  it('returns null without the middleware-set identity headers', async () => {
    expect(await userFrom({ 'x-duar-user-id': 'u1' })).toBeNull()
  })

  it('parses x-duar-actions: absent → undefined (standard mode), empty → [], list → array', async () => {
    expect((await userFrom(BASE))!.actions).toBeUndefined()
    expect((await userFrom({ ...BASE, 'x-duar-actions': '' }))!.actions).toEqual([])
    expect((await userFrom({ ...BASE, 'x-duar-actions': 'notes:create,notes:delete' }))!.actions)
      .toEqual(['notes:create', 'notes:delete'])
  })

  it('org: absent → null; orgIsPublic only for the exact string "true"', async () => {
    const user = (await userFrom(BASE))!
    expect([user.orgId, user.orgSlug, user.orgIsPublic]).toEqual([null, null, false])
    for (const v of ['TRUE', '1', 'yes', '']) {
      expect((await userFrom({ ...BASE, 'x-duar-org-public': v }))!.orgIsPublic).toBe(false)
    }
    expect((await userFrom({ ...BASE, 'x-duar-org-public': 'true' }))!.orgIsPublic).toBe(true)
  })
})
