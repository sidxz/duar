import type { ReactNode } from 'react'
import { useAuthz } from './authz-hooks'

export interface AuthzGuardProps {
  children: ReactNode
  fallback: ReactNode
  loading?: ReactNode
}

export function AuthzGuard({ children, fallback, loading = null }: AuthzGuardProps) {
  const { isAuthenticated, isLoading } = useAuthz()

  if (isLoading) return <>{loading}</>
  if (!isAuthenticated) return <>{fallback}</>
  return <>{children}</>
}
