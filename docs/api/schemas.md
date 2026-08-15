# Request & Response Schemas

Pydantic models used across the Duar API. All UUIDs are v4 strings. Timestamps are ISO 8601.

---

## Auth

### TokenResponse

Returned by `POST /auth/token` and `POST /auth/refresh`.

| Field | Type | Description |
|---|---|---|
| `access_token` | string | RS256-signed JWT |
| `refresh_token` | string | Opaque refresh token |
| `token_type` | string | Always `"bearer"` |
| `expires_in` | int | Access token TTL in seconds |

### RefreshRequest

| Field | Type | Description |
|---|---|---|
| `refresh_token` | string | Refresh token to exchange |

### SelectWorkspaceRequest

| Field | Type | Description |
|---|---|---|
| `code` | string | Authorization code from OAuth callback |
| `workspace_id` | UUID | Workspace to authenticate into |
| `code_verifier` | string | PKCE verifier (43--128 chars) |

### TokenPayload (JWT Claims)

Not returned directly. Describes the access token payload.

| Claim | Type | Description |
|---|---|---|
| `sub` | UUID | User ID |
| `email` | string | User email |
| `name` | string | Display name |
| `wid` | UUID | Active workspace ID |
| `wslug` | string | Workspace slug |
| `wrole` | string | Workspace role: `owner`, `admin`, `editor`, `viewer` |
| `groups` | UUID[] | Group IDs in the active workspace |

### AuthzResolveRequest

| Field | Type | Required | Description |
|---|---|---|---|
| `idp_token` | string | Yes | Raw IdP token |
| `provider` | string | Yes | `google`, `github`, `entra_id` |
| `workspace_id` | UUID | No | Omit to get workspace list |

### AuthzResolveResponse

| Field | Type | Description |
|---|---|---|
| `user` | object | `{id, email, name}` |
| `workspace` | object or null | `{id, slug, role}` (when workspace_id provided) |
| `authz_token` | string or null | Signed authz JWT (when workspace_id provided) |
| `expires_in` | int or null | Token TTL in seconds |
| `workspaces` | array or null | `[{id, name, slug, role}]` (when workspace_id omitted) |

---

## Users

### UserResponse

| Field | Type | Description |
|---|---|---|
| `id` | UUID | User ID |
| `email` | string | Email address |
| `name` | string | Display name |
| `avatar_url` | string or null | Avatar URL |
| `is_active` | bool | Account active status |
| `created_at` | datetime | Creation timestamp |

### UserUpdateRequest

| Field | Type | Description |
|---|---|---|
| `name` | string or null | New display name |
| `avatar_url` | string or null | New avatar URL (http/https only) |

---

## Workspaces

### WorkspaceCreateRequest

| Field | Type | Constraints | Description |
|---|---|---|---|
| `name` | string | 1--255 chars | Display name |
| `slug` | string | 1--100, `^[a-z0-9][a-z0-9-]*[a-z0-9]$` | URL-safe identifier |
| `description` | string or null | -- | Optional description |

### WorkspaceResponse

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Workspace ID |
| `slug` | string | URL-safe identifier |
| `name` | string | Display name |
| `description` | string or null | Description |
| `created_by` | UUID or null | Creator user ID |
| `created_at` | datetime | Creation timestamp |

### WorkspaceMemberResponse

| Field | Type | Description |
|---|---|---|
| `user_id` | UUID | Member's user ID |
| `email` | string | Email |
| `name` | string | Display name |
| `avatar_url` | string or null | Avatar URL |
| `role` | string | `owner`, `admin`, `editor`, or `viewer` |
| `joined_at` | datetime | Join timestamp |

### InviteMemberRequest

| Field | Type | Default | Description |
|---|---|---|---|
| `email` | string | -- | Email of user to invite |
| `role` | string | `"viewer"` | One of: `owner`, `admin`, `editor`, `viewer` |

### UpdateMemberRoleRequest

| Field | Type | Description |
|---|---|---|
| `role` | string | One of: `owner`, `admin`, `editor`, `viewer` |

---

## Groups

### GroupCreateRequest

| Field | Type | Constraints | Description |
|---|---|---|---|
| `name` | string | 1--255 chars | Group name |
| `description` | string or null | -- | Optional description |

### GroupResponse

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Group ID |
| `workspace_id` | UUID | Parent workspace ID |
| `name` | string | Group name |
| `description` | string or null | Description |
| `created_by` | UUID | Creator user ID |
| `created_at` | datetime | Creation timestamp |

---

## Permissions

### PermissionCheckRequest

| Field | Type | Description |
|---|---|---|
| `checks` | PermissionCheckItem[] | Up to 100 items |

Each `PermissionCheckItem`:

| Field | Type | Description |
|---|---|---|
| `service_name` | string | Service name |
| `resource_type` | string | Resource type |
| `resource_id` | UUID | Resource ID |
| `action` | string | `"view"` or `"edit"` |

### PermissionCheckResponse

| Field | Type | Description |
|---|---|---|
| `results` | PermissionCheckResult[] | One result per check item |

Each result has the same fields as the check item plus `allowed` (bool).

### RegisterResourceRequest

| Field | Type | Default | Description |
|---|---|---|---|
| `service_name` | string | -- | Service name |
| `resource_type` | string | -- | Resource type |
| `resource_id` | UUID | -- | Resource ID |
| `workspace_id` | UUID | -- | Workspace ID |
| `owner_id` | UUID | -- | Owner user ID |
| `visibility` | string | `"workspace"` | `"private"` or `"workspace"` |

### ShareRequest

| Field | Type | Description |
|---|---|---|
| `grantee_type` | string | `"user"` or `"group"` |
| `grantee_id` | UUID | User or group ID |
| `permission` | string | `"view"` or `"edit"` |

### AccessibleResourcesRequest

| Field | Type | Description |
|---|---|---|
| `service_name` | string | Service to query |
| `resource_type` | string | Resource type |
| `action` | string | `"view"` or `"edit"` |
| `workspace_id` | UUID | Must match JWT workspace |
| `limit` | int or null | 1--10000, optional |

### ResourcePermissionResponse

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Permission record ID |
| `service_name` | string | Service name |
| `resource_type` | string | Resource type |
| `resource_id` | UUID | Resource ID |
| `workspace_id` | UUID | Workspace ID |
| `owner_id` | UUID or null | Owner user ID |
| `visibility` | string | `"private"` or `"workspace"` |
| `created_at` | datetime | Registration timestamp |
| `shares` | ResourceShareResponse[] | Active shares |

Each `ResourceShareResponse`:

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Share record ID |
| `grantee_type` | string | `"user"` or `"group"` |
| `grantee_id` | UUID | Grantee ID |
| `permission` | string | `"view"` or `"edit"` |
| `granted_by` | UUID or null | Who created the share |
| `granted_at` | datetime | Share creation timestamp |

---

## Roles

### RegisterActionsRequest

| Field | Type | Description |
|---|---|---|
| `service_name` | string | Service registering actions |
| `actions` | ActionDefinition[] | Actions to register |

Each `ActionDefinition`:

| Field | Type | Description |
|---|---|---|
| `action` | string | Identifier matching `^[a-z][a-z0-9_.:-]*$` |
| `description` | string or null | Human-readable description |

### ServiceActionResponse

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Action record ID |
| `service_name` | string | Service name |
| `action` | string | Action identifier |
| `description` | string or null | Description |
| `created_at` | datetime | Registration timestamp |

### CheckActionRequest

| Field | Type | Description |
|---|---|---|
| `service_name` | string | Service to check |
| `action` | string | Action identifier |
| `workspace_id` | UUID | Must match JWT workspace |

### CheckActionResponse

| Field | Type | Description |
|---|---|---|
| `allowed` | bool | Whether the action is permitted |
| `roles` | string[] | Role names that grant this action |

### UserActionsResponse

| Field | Type | Description |
|---|---|---|
| `actions` | string[] | Action identifiers the user can perform |
