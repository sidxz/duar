export { DuarAuthProvider, type DuarAuthContextValue, type DuarAuthProviderProps } from './provider'
export { useAuth, useUser, useHasRole, useAuthFetch } from './hooks'
export { AuthGuard, type AuthGuardProps } from './auth-guard'
export { AuthCallback, type AuthCallbackProps, type WorkspaceSelectorProps } from './callback'

// Authz-mode provider, hooks, guard, and callback
export { AuthzProvider, AuthzContext, type AuthzContextValue, type AuthzProviderProps } from './authz-provider'
export { useAuthz, useAuthzUser, useAuthzHasRole, useAuthzFetch } from './authz-hooks'
export { AuthzGuard, type AuthzGuardProps } from './authz-guard'
export { AuthzCallback, type AuthzCallbackProps, type AuthzWorkspaceSelectorProps } from './authz-callback'

// Re-export commonly used types from @duar-auth/js
export type {
  DuarConfig,
  DuarUser,
  WorkspaceOption,
  WorkspaceRole,
  DuarAuthzConfig,
  AuthzTokenStore,
  AuthzResolveResponse,
  AuthState,
  AuthzCallbackResult,
  WorkspaceMember,
  GroupInfo,
  GroupMemberInfo,
  UserProfile,
} from '@duar-auth/js'
