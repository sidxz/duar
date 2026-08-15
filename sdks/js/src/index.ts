// Browser + types entry point
export { DuarAuth } from './client'
export { generateCodeVerifier, deriveCodeChallenge } from './pkce'
export { LocalStorageStore, SessionStorageStore, MemoryStore } from './storage'
export { parseJwt, isTokenExpired, tokenToUser, authzTokenToUser } from './jwt-utils'

export type {
  DuarConfig,
  TokenStore,
  TokenResponse,
  WorkspaceOption,
  DuarUser,
  WorkspaceRole,
  JWTPayload,
  AuthzJWTPayload,
  PermissionCheck,
  PermissionResult,
  RegisterResourceRequest,
  ShareRequest,
  AccessibleResult,
  ActionDefinition,
  VerifyOptions,
} from './types'

// Authz (direct IdP) mode
export { DuarAuthz } from './authz-client'
export { AuthzLocalStorageStore, AuthzMemoryStore } from './authz-storage'

export { IdpConfigs } from './authz-types'
export type {
  DuarAuthzConfig,
  AuthzTokenStore,
  AuthzResolveResponse,
  AuthzUserInfo,
  AuthzWorkspaceOption,
  AuthzWorkspaceInfo,
  AuthState,
  AuthzCallbackResult,
  IdpConfig,
  UserIdentity,
  WorkspaceMember,
  GroupInfo,
  GroupMemberInfo,
  UserProfile,
} from './authz-types'
