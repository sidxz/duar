import {
  createContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  DuarAuthz,
  type DuarAuthzConfig,
  type DuarUser,
  type AuthzResolveResponse,
  type AuthState,
} from '@duar-auth/js'

export interface AuthzContextValue {
  client: DuarAuthz
  user: DuarUser | null
  isLoading: boolean
  isAuthenticated: boolean
  /**
   * Derived auth state. ``needs_reauth`` means a valid authz token exists but
   * the memory-only IdP token is gone (e.g. after a reload) — the session
   * cannot authenticate requests until the user re-auths with the IdP.
   */
  authState: AuthState
  /** Convenience: ``authState === 'needs_reauth'``. */
  needsReauth: boolean
  login(provider: string): void
  /** Start a silent (``prompt=none``) re-auth redirect. Returns false if it no-ops. */
  silentLogin(provider?: string): boolean
  /** Read & clear the post-reauth return path (same-origin validated). */
  consumeReturnTo(): string | null
  resolve(idpToken: string, provider: string): Promise<AuthzResolveResponse>
  selectWorkspace(idpToken: string, provider: string, workspaceId: string): Promise<void>
  logout(): void
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>
  fetchJson: <T>(input: RequestInfo | URL, init?: RequestInit) => Promise<T>
}

const AuthzContext = createContext<AuthzContextValue | null>(null)

export interface AuthzProviderProps {
  config?: DuarAuthzConfig
  client?: DuarAuthz
  /**
   * When true, automatically attempt a silent (``prompt=none``) re-auth on
   * mount if the session is in the ``needs_reauth`` state (e.g. after a page
   * reload). Off by default — opt in for seamless persistence. The redirect
   * has a built-in loop guard; if silent auth fails the callback falls back to
   * interactive login. Requires the provider to be configured in ``idps``.
   */
  autoReauth?: boolean
  children: ReactNode
}

export function AuthzProvider({
  config,
  client: externalClient,
  autoReauth = false,
  children,
}: AuthzProviderProps) {
  const clientRef = useRef<DuarAuthz | null>(externalClient ?? null)
  if (!clientRef.current) {
    if (!config) throw new Error('AuthzProvider requires either config or client prop')
    clientRef.current = new DuarAuthz(config)
  }
  const client = clientRef.current
  const ownsClient = !externalClient

  const [user, setUser] = useState<DuarUser | null>(() => client.getUser())
  const [authState, setAuthState] = useState<AuthState>(() => client.getAuthState())
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const state = client.getAuthState()
    setUser(client.getUser())
    setAuthState(state)

    // Opt-in seamless re-auth: if the IdP token is gone but an authz token
    // survives, bounce through the IdP with prompt=none. silentLogin() has its
    // own loop guard and navigates away on success; keep the loading state up
    // so we don't flash a login screen before the redirect happens.
    // silentLogin() throws if the stored provider isn't configured in `idps`;
    // swallow it here so a stale/unknown provider falls back to showing login
    // instead of crashing the whole subtree from inside the effect.
    let reauthing = false
    if (autoReauth && state === 'needs_reauth') {
      try {
        reauthing = client.silentLogin()
      } catch {
        reauthing = false
      }
    }
    setIsLoading(reauthing)

    const unsub = client.onAuthStateChange((u) => {
      setUser(u)
      setAuthState(client.getAuthState())
    })

    return () => {
      unsub()
      if (ownsClient) client.destroy()
    }
  }, [client, ownsClient, autoReauth])

  const value: AuthzContextValue = {
    client,
    user,
    isLoading,
    isAuthenticated: authState === 'authenticated',
    authState,
    needsReauth: authState === 'needs_reauth',
    login: (provider) => client.login(provider),
    silentLogin: (provider) => client.silentLogin(provider),
    consumeReturnTo: () => client.consumeReturnTo(),
    resolve: (idpToken, provider) => client.resolve(idpToken, provider),
    selectWorkspace: (idpToken, provider, workspaceId) =>
      client.selectWorkspace(idpToken, provider, workspaceId),
    logout: () => client.logout(),
    fetch: (input, init) => client.fetch(input, init),
    fetchJson: <T,>(input: RequestInfo | URL, init?: RequestInit) =>
      client.fetchJson<T>(input, init),
  }

  return (
    <AuthzContext.Provider value={value}>
      {children}
    </AuthzContext.Provider>
  )
}

export { AuthzContext }
