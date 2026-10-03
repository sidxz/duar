// Server (Node.js / Edge) entry point
export { verifyToken, payloadToUser } from './jwt-verifier'
export { PermissionClient } from './permissions'
export { RoleClient } from './roles'
export { verifyM2mToken, fetchWhoami, M2mTokenClient } from './m2m'

export type {
  DuarUser,
  WorkspaceRole,
  JWTPayload,
  VerifyOptions,
  PermissionCheck,
  PermissionResult,
  RegisterResourceRequest,
  ShareRequest,
  AccessibleResult,
  ActionDefinition,
  M2mJWTPayload,
  WhoamiResponse,
  M2mVerifyOptions,
  SystemAuth,
} from './types'
