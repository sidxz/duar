import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { AuthzWorkspaceOption, DuarUser } from '@duar-auth/js'
import { useAuthz } from './authz-hooks'

export interface AuthzWorkspaceSelectorProps {
  workspaces: AuthzWorkspaceOption[]
  onSelect: (workspaceId: string) => void
  isLoading: boolean
}

export interface AuthzCallbackProps {
  /**
   * Called after successful authentication. `returnTo` is the same-origin path
   * the user was on before a silent re-auth redirect (null if none) — navigate
   * there to restore their place.
   */
  onSuccess: (user: DuarUser, returnTo?: string | null) => void
  /** Called on error. */
  onError?: (error: Error) => void
  /**
   * Called when a silent (`prompt=none`) re-auth could not complete without
   * user interaction. The stale session has already been cleared. Typically
   * trigger an interactive `login(provider)` or redirect to your login page.
   * If omitted, this falls through to `onError`.
   */
  onSilentReauthFailed?: (returnTo: string | null) => void
  /** Shown while loading. */
  loadingComponent?: ReactNode
  /** Shown on error. */
  errorComponent?: (error: Error) => ReactNode
  /** Custom workspace selector UI. */
  workspaceSelector?: (props: AuthzWorkspaceSelectorProps) => ReactNode
}

// Capture hash at module load — must survive React StrictMode double-mount.
// The hash contains the IdP token and is only present once (on redirect back).
const capturedCallback =
  typeof window !== 'undefined'
    ? (() => {
        const hash = window.location.hash.substring(1)
        if (hash) window.history.replaceState({}, '', window.location.pathname)
        return hash
      })()
    : ''

/**
 * AuthZ mode OAuth callback component. Interprets the IdP response via the
 * SDK's `handleCallback` (nonce-verified), resolves workspaces, auto-selects
 * if one, shows a picker if multiple, and recovers from failed silent re-auth.
 *
 * Drop-in equivalent of proxy mode's `AuthCallback`.
 */
export function AuthzCallback({
  onSuccess,
  onError,
  onSilentReauthFailed,
  loadingComponent,
  errorComponent,
  workspaceSelector,
}: AuthzCallbackProps) {
  const { resolve, selectWorkspace, client, logout } = useAuthz()
  const [workspaces, setWorkspaces] = useState<AuthzWorkspaceOption[]>([])
  const [idpToken, setIdpToken] = useState<string | null>(null)
  const [provider, setProvider] = useState<string>('google')
  const [returnTo, setReturnTo] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [selecting, setSelecting] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const resolvedRef = useRef(false)

  useEffect(() => {
    if (resolvedRef.current) return
    resolvedRef.current = true

    // Delegate parsing + nonce verification to the SDK (single tested path).
    let result
    try {
      result = client.handleCallback(capturedCallback)
    } catch (e) {
      const err = e instanceof Error ? e : new Error('Authentication failed')
      setError(err)
      setLoading(false)
      onError?.(err)
      return
    }

    if (!result) {
      const err = new Error('No IdP response in callback URL')
      setError(err)
      setLoading(false)
      onError?.(err)
      return
    }

    if (result.status === 'silent_failed') {
      // Silent re-auth needs interaction. Clear the stale session so the app
      // shows login instead of a broken page, then hand off to the consumer.
      logout()
      setLoading(false)
      if (onSilentReauthFailed) onSilentReauthFailed(result.returnTo)
      else onError?.(new Error('Silent re-authentication failed — interactive login required'))
      return
    }

    const { idpToken: token, provider: prov, returnTo: ret } = result
    setIdpToken(token)
    setProvider(prov)
    setReturnTo(ret)

    resolve(token, prov)
      .then(async (resolved) => {
        if (!resolved.workspaces || resolved.workspaces.length === 0) {
          throw new Error('No workspaces available. Ask an admin to add you to a workspace.')
        }
        if (resolved.workspaces.length === 1) {
          await selectWorkspace(token, prov, resolved.workspaces[0].id)
          onSuccess(client.getUser()!, ret)
          return
        }
        setWorkspaces(resolved.workspaces)
        setLoading(false)
      })
      .catch((e: unknown) => {
        const err = e instanceof Error ? e : new Error('Authentication failed')
        setError(err)
        setLoading(false)
        onError?.(err)
      })
  }, [])

  async function handleSelectWorkspace(workspaceId: string) {
    if (!idpToken) return
    setSelecting(true)
    try {
      await selectWorkspace(idpToken, provider, workspaceId)
      onSuccess(client.getUser()!, returnTo)
    } catch (e: unknown) {
      const err = e instanceof Error ? e : new Error('Workspace selection failed')
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
