import {
  createContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  DuarAuth,
  type DuarConfig,
  type DuarUser,
  type WorkspaceOption,
} from '@duar-auth/js'

export interface DuarAuthContextValue {
  client: DuarAuth
  user: DuarUser | null
  isLoading: boolean
  isAuthenticated: boolean
  login(provider: string): Promise<void>
  logout(): void
  getProviders(): Promise<string[]>
  getWorkspaces(code: string): Promise<WorkspaceOption[]>
  selectWorkspace(code: string, workspaceId: string): Promise<void>
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>
  fetchJson: <T>(input: RequestInfo | URL, init?: RequestInit) => Promise<T>
}

const DuarAuthContext = createContext<DuarAuthContextValue | null>(null)

export interface DuarAuthProviderProps {
  /** Provide config to let the provider create a DuarAuth instance, or provide a pre-created client. */
  config?: DuarConfig
  /** Pre-created DuarAuth client. Takes precedence over config. */
  client?: DuarAuth
  children: ReactNode
}

export function DuarAuthProvider({
  config,
  client: externalClient,
  children,
}: DuarAuthProviderProps) {
  const clientRef = useRef<DuarAuth | null>(externalClient ?? null)
  if (!clientRef.current) {
    if (!config) throw new Error('DuarAuthProvider requires either config or client prop')
    clientRef.current = new DuarAuth(config)
  }
  const client = clientRef.current
  const ownsClient = !externalClient

  const [user, setUser] = useState<DuarUser | null>(() => client.getUser())
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    setUser(client.getUser())
    setIsLoading(false)

    const unsub = client.onAuthStateChange((u) => {
      setUser(u)
    })

    return () => {
      unsub()
      if (ownsClient) client.destroy()
    }
  }, [client, ownsClient])

  const value: DuarAuthContextValue = {
    client,
    user,
    isLoading,
    isAuthenticated: user !== null,
    login: (provider) => client.login(provider),
    logout: () => client.logout(),
    getProviders: () => client.getProviders(),
    getWorkspaces: (code) => client.getWorkspaces(code),
    selectWorkspace: async (code, workspaceId) => {
      await client.selectWorkspace(code, workspaceId)
    },
    fetch: (input, init) => client.fetch(input, init),
    fetchJson: <T,>(input: RequestInfo | URL, init?: RequestInit) => client.fetchJson<T>(input, init),
  }

  return (
    <DuarAuthContext.Provider value={value}>
      {children}
    </DuarAuthContext.Provider>
  )
}

export { DuarAuthContext }
