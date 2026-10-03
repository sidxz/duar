import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { RoleClient } from '../roles'

describe('RoleClient', () => {
  let client: RoleClient

  beforeEach(() => {
    client = new RoleClient('http://localhost:9003', 'notes-service', 'svc-key-123')
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('registerActions sends correct request', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    )

    await client.registerActions([
      { action: 'notes:export', description: 'Export notes' },
      { action: 'notes:import' },
    ])

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('http://localhost:9003/roles/actions/register')
    expect(init?.method).toBe('POST')

    const body = JSON.parse(init?.body as string)
    expect(body.service_name).toBe('notes-service')
    expect(body.actions).toHaveLength(2)

    const headers = init?.headers as Record<string, string>
    expect(headers['X-Service-Key']).toBe('svc-key-123')
    expect(headers['Authorization']).toBeUndefined()
  })

  it('checkAction returns boolean', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ allowed: true }), { status: 200 }),
    )

    const result = await client.checkAction('user-jwt', 'notes:export', 'ws-1')
    expect(result).toBe(true)

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('http://localhost:9003/roles/check-action')
    const headers = init?.headers as Record<string, string>
    expect(headers['Authorization']).toBe('Bearer user-jwt')
    expect(headers['X-Service-Key']).toBe('svc-key-123')

    const body = JSON.parse(init?.body as string)
    expect(body.action).toBe('notes:export')
    expect(body.workspace_id).toBe('ws-1')
  })

  it('getUserActions returns action list', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ actions: ['notes:export', 'notes:import'] }), { status: 200 }),
    )

    const actions = await client.getUserActions('user-jwt', 'ws-1')
    expect(actions).toEqual(['notes:export', 'notes:import'])

    const [url] = vi.mocked(fetch).mock.calls[0]
    expect(url).toContain('/roles/user-actions?')
    expect(url).toContain('service_name=notes-service')
    expect(url).toContain('workspace_id=ws-1')
  })

  it('works without service key', async () => {
    const noKeyClient = new RoleClient('http://localhost:9003', 'notes-service')

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ allowed: false }), { status: 200 }),
    )

    await noKeyClient.checkAction('user-jwt', 'notes:export', 'ws-1')

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = init?.headers as Record<string, string>
    expect(headers['X-Service-Key']).toBeUndefined()
    expect(headers['Authorization']).toBe('Bearer user-jwt')
  })
})
