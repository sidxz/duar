'use client'
export {
  DuarAuthProvider,
  useAuth,
  useUser,
  useHasRole,
  useAuthFetch,
  AuthGuard,
  AuthCallback,
} from '@duar-auth/react'

export type {
  DuarAuthProviderProps,
  DuarAuthContextValue,
  AuthGuardProps,
  AuthCallbackProps,
  WorkspaceSelectorProps,
  DuarConfig,
  DuarUser,
  WorkspaceOption,
  WorkspaceRole,
} from '@duar-auth/react'

// Authz-mode components, hooks, and types
export {
  AuthzProvider,
  useAuthz,
  useAuthzUser,
  useAuthzHasRole,
  useAuthzFetch,
  AuthzGuard,
  AuthzCallback,
} from '@duar-auth/react'

export type {
  AuthzProviderProps,
  AuthzContextValue,
  AuthzGuardProps,
  AuthzCallbackProps,
  AuthzWorkspaceSelectorProps,
  DuarAuthzConfig,
  AuthzTokenStore,
  AuthzResolveResponse,
  AuthState,
  AuthzCallbackResult,
  WorkspaceMember,
  GroupInfo,
  GroupMemberInfo,
  UserProfile,
} from '@duar-auth/react'
