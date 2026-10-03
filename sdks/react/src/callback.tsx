import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { DuarUser, WorkspaceOption } from '@duar-auth/js'
import { useAuth } from './hooks'

export interface WorkspaceSelectorProps {
  workspaces: WorkspaceOption[]
  onSelect: (workspaceId: string) => void
  isLoading: boolean
}

export interface AuthCallbackProps {
  /** Called after successful authentication with the user object. */
  onSuccess: (user: DuarUser) => void
  /** Called on error. */
  onError?: (error: Error) => void
  /** Shown while loading. */
  loadingComponent?: ReactNode
  /** Shown on error. */
  errorComponent?: (error: Error) => ReactNode
  /** Custom workspace selector UI. */
  workspaceSelector?: (props: WorkspaceSelectorProps) => ReactNode
}

/**
 * OAuth callback route component. Reads `?code=` from URL, fetches workspaces,
 * auto-selects if one, shows picker if multiple. Router-agnostic.
 */
export function AuthCallback({
  onSuccess,
  onError,
  loadingComponent,
  errorComponent,
  workspaceSelector,
}: AuthCallbackProps) {
  const { getWorkspaces, selectWorkspace, client } = useAuth()
  const [workspaces, setWorkspaces] = useState<WorkspaceOption[]>([])
  const [loading, setLoading] = useState(true)
  const [selecting, setSelecting] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const processedRef = useRef(false)

  const code =
    typeof window !== 'undefined'
      ? new URLSearchParams(window.location.search).get('code')
      : null

  useEffect(() => {
    // verifyCallbackState() consumes the one-shot state key, so this effect
    // must not run twice (React StrictMode double-mounts) — same guard as
    // AuthzCallback's resolvedRef.
    if (processedRef.current) return
    processedRef.current = true

    if (!code) {
      const err = new Error('Missing authorization code in callback URL')
      setError(err)
      setLoading(false)
      onError?.(err)
      return
    }

    // Verify OAuth state parameter to prevent CSRF
    try {
      client.verifyCallbackState()
    } catch (e: unknown) {
      const err = e instanceof Error ? e : new Error('OAuth state verification failed')
      setError(err)
      setLoading(false)
      onError?.(err)
      return
    }

    getWorkspaces(code)
      .then(async (ws) => {
        if (ws.length === 0) {
          throw new Error('No workspaces found. Ask an admin to invite you.')
        }
        if (ws.length === 1) {
          await selectWorkspace(code, ws[0].id)
          onSuccess(client.getUser()!)
          return
        }
        setWorkspaces(ws)
        setLoading(false)
      })
      .catch((e: unknown) => {
        const err = e instanceof Error ? e : new Error('Failed to load workspaces')
        setError(err)
        setLoading(false)
        onError?.(err)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

  async function handleSelectWorkspace(workspaceId: string) {
    if (!code) return
    setSelecting(true)
    try {
      await selectWorkspace(code, workspaceId)
      onSuccess(client.getUser()!)
    } catch (e: unknown) {
      const err = e instanceof Error ? e : new Error('Token exchange failed')
      setError(err)
      setSelecting(false)
      onError?.(err)
    }
  }

  if (error) {
    if (errorComponent) return <>{errorComponent(error)}</>
    return <div>{error.message}</div>
  }

  if (loading) {
    if (loadingComponent) return <>{loadingComponent}</>
    return <div>Signing you in...</div>
  }

  if (workspaces.length > 0) {
    if (workspaceSelector) {
      return (
        <>
          {workspaceSelector({
            workspaces,
            onSelect: handleSelectWorkspace,
            isLoading: selecting,
          })}
        </>
      )
    }

    // Default workspace picker
    return (
      <div>
        <h2>Select Workspace</h2>
        {workspaces.map((ws) => (
          <button
            key={ws.id}
            onClick={() => handleSelectWorkspace(ws.id)}
            disabled={selecting}
          >
            {ws.name} ({ws.slug}) — {ws.role}
          </button>
        ))}
      </div>
    )
  }

  return null
}
