import type { ReactNode } from 'react'
import { useAuth } from './hooks'

export interface AuthGuardProps {
  children: ReactNode
  /** Rendered when the user is not authenticated (e.g. a login page). */
  fallback: ReactNode
  /** Rendered while checking auth state. Defaults to null. */
  loading?: ReactNode
}

/**
 * Renders children if authenticated, fallback if not, loading while checking.
 */
export function AuthGuard({ children, fallback, loading = null }: AuthGuardProps) {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) return <>{loading}</>
  if (!isAuthenticated) return <>{fallback}</>
  return <>{children}</>
}
