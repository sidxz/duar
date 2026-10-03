import type { ActionDefinition } from './types'
import { warnIfInsecure } from './warn-insecure'

/**
 * Server-side client for the Duar RBAC role/action API.
 * Mirrors the Python SDK's `RoleClient`.
 */
export class RoleClient {
  private readonly baseUrl: string
  private readonly serviceName: string
  private readonly serviceKey: string | undefined

  constructor(baseUrl: string, serviceName: string, serviceKey?: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, '')
    this.serviceName = serviceName
    this.serviceKey = serviceKey
    warnIfInsecure(this.baseUrl, 'RoleClient')
  }

  /** Register actions for this service (service-key only). */
  async registerActions(actions: ActionDefinition[]): Promise<void> {
    const res = await fetch(`${this.baseUrl}/roles/actions/register`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        service_name: this.serviceName,
        actions,
      }),
    })
    if (!res.ok) throw new Error(`Register actions failed: ${res.status}`)
  }

  /** Check if the current user can perform an action. */
  async checkAction(
    token: string,
    action: string,
    workspaceId: string,
  ): Promise<boolean> {
    const res = await fetch(`${this.baseUrl}/roles/check-action`, {
      method: 'POST',
      headers: this.headers(token),
      body: JSON.stringify({
        service_name: this.serviceName,
        action,
        workspace_id: workspaceId,
      }),
    })
    if (!res.ok) throw new Error(`Check action failed: ${res.status}`)
    const data = await res.json()
    return data.allowed
  }

  /** List all actions the current user can perform in a workspace. */
  async getUserActions(
    token: string,
    workspaceId: string,
  ): Promise<string[]> {
    const params = new URLSearchParams({
      service_name: this.serviceName,
      workspace_id: workspaceId,
    })
    const res = await fetch(
      `${this.baseUrl}/roles/user-actions?${params}`,
      { headers: this.headers(token) },
    )
    if (!res.ok) throw new Error(`Get user actions failed: ${res.status}`)
    const data = await res.json()
    return data.actions
  }

  private headers(token?: string): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' }
    if (this.serviceKey) h['X-Service-Key'] = this.serviceKey
    if (token) h['Authorization'] = `Bearer ${token}`
    return h
  }
}
