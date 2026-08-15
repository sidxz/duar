import { useContext } from 'react'
import type { DuarUser, WorkspaceRole } from '@duar-auth/js'
import { AuthzContext, type AuthzContextValue } from './authz-provider'

const ROLE_HIERARCHY: WorkspaceRole[] = ['viewer', 'editor', 'admin', 'owner']

export function useAuthz(): AuthzContextValue {
  const ctx = useContext(AuthzContext)
  if (!ctx) {
    throw new Error('useAuthz must be used within an AuthzProvider')
  }
  return ctx
}

export function useAuthzUser(): DuarUser {
  const { user } = useAuthz()
  if (!user) {
    throw new Error('useAuthzUser: no authenticated user')
  }
  return user
}

export function useAuthzHasRole(minimum: WorkspaceRole): boolean {
  const { user } = useAuthz()
  if (!user) return false
  const userLevel = ROLE_HIERARCHY.indexOf(user.workspaceRole)
  const requiredLevel = ROLE_HIERARCHY.indexOf(minimum)
  if (requiredLevel === -1) return false
  return userLevel >= requiredLevel
}

export function useAuthzFetch(): (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response> {
  const { fetch: authzFetch } = useAuthz()
  return authzFetch
}
