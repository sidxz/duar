import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { PermissionClient } from '../permissions'

describe('PermissionClient', () => {
  let client: PermissionClient

  beforeEach(() => {
    client = new PermissionClient('http://localhost:9003', 'notes-service', 'svc-key-123')
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('check sends correct request', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          results: [
            { service_name: 'notes-service', resource_type: 'note', resource_id: 'n1', action: 'view', allowed: true },
          ],
        }),
        { status: 200 },
      ),
    )

    const results = await client.check('user-jwt', [
      { service_name: 'notes-service', resource_type: 'note', resource_id: 'n1', action: 'view' },
    ])

    expect(results).toHaveLength(1)
    expect(results[0].allowed).toBe(true)

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('http://localhost:9003/permissions/check')
    expect(init?.method).toBe('POST')
    const headers = init?.headers as Record<string, string>
    expect(headers['X-Service-Key']).toBe('svc-key-123')
    expect(headers['Authorization']).toBe('Bearer user-jwt')
  })

  it('can returns boolean', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          results: [
            { service_name: 'notes-service', resource_type: 'note', resource_id: 'n1', action: 'edit', allowed: false },
          ],
        }),
        { status: 200 },
      ),
    )

    const result = await client.can('user-jwt', 'note', 'n1', 'edit')
    expect(result).toBe(false)
  })

  it('registerResource sends correct request without user token', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'perm-1' }), { status: 200 }),
    )

    await client.registerResource({
      service_name: 'notes-service',
      resource_type: 'note',
      resource_id: 'n1',
      workspace_id: 'ws-1',
      owner_id: 'user-1',
    })

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = init?.headers as Record<string, string>
    expect(headers['X-Service-Key']).toBe('svc-key-123')
    expect(headers['Authorization']).toBeUndefined()
  })

  it('accessible sends correct request', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({ resource_ids: ['n1', 'n2'], has_full_access: false }),
        { status: 200 },
      ),
    )

    const result = await client.accessible('user-jwt', 'note', 'view', 'ws-1', 100)
    expect(result.resource_ids).toEqual(['n1', 'n2'])
    expect(result.has_full_access).toBe(false)

    const body = JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string)
    expect(body.limit).toBe(100)
    expect(body.workspace_id).toBe('ws-1')
  })

  it('share resolves permission ID then shares', async () => {
    vi.mocked(fetch)
      // Lookup call
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: 'perm-42' }), { status: 200 }),
      )
      // Share call
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), { status: 200 }),
      )

    await client.share('user-jwt', 'note', 'n1', {
      grantee_type: 'user',
      grantee_id: 'user-2',
      permission: 'view',
    })

    expect(fetch).toHaveBeenCalledTimes(2)
    const [lookupUrl] = vi.mocked(fetch).mock.calls[0]
    expect(lookupUrl).toBe('http://localhost:9003/permissions/resource/notes-service/note/n1')
    const [shareUrl] = vi.mocked(fetch).mock.calls[1]
    expect(shareUrl).toBe('http://localhost:9003/permissions/perm-42/share')
  })
})
