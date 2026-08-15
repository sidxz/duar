# Workspaces

Workspaces are the tenant isolation boundary. Every user, group, role, and resource is scoped to a workspace. A user can belong to multiple workspaces, but their JWT always reflects one active workspace.

```python
from duar_auth.dependencies import require_role
from duar_auth.types import AuthenticatedUser

@router.post("/projects")
async def create_project(
    user: AuthenticatedUser = Depends(require_role("editor")),
):
    # user.workspace_id — active workspace UUID
    # user.workspace_role — "owner", "admin", "editor", or "viewer"
    return await create(user.workspace_id, user.user_id)
```

## Workspace Roles

Every member has exactly one role per workspace. Roles form a strict hierarchy:

```
owner > admin > editor > viewer
```

| Permission | viewer | editor | admin | owner |
|---|---|---|---|---|
| View workspace and resources | yes | yes | yes | yes |
| Create and edit own resources | -- | yes | yes | yes |
| Edit all resources | -- | -- | yes | yes |
| Manage members (invite, remove, change role) | -- | -- | yes | yes |
| Update workspace settings | -- | -- | yes | yes |
| Delete workspace | -- | -- | -- | yes |

### JWT Claims

When a user selects a workspace, the access token includes:

```json
{
  "wid": "550e8400-e29b-41d4-a716-446655440000",
  "wslug": "acme-corp",
  "wrole": "editor",
  "groups": ["group-uuid-1", "group-uuid-2"]
}
```

The SDK's `AuthenticatedUser` exposes these as typed properties:

```python
user.workspace_id      # UUID from wid
user.workspace_slug    # str from wslug
user.workspace_role    # str from wrole
user.groups            # list[UUID] from groups
user.is_admin          # True if role is admin or owner
user.is_editor         # True if role is editor, admin, or owner
user.has_role("admin") # True if role >= admin in hierarchy
```

### Role Enforcement

Use `require_role` to enforce a minimum role on a route:

```python
from duar_auth.dependencies import require_role

@router.delete("/workspace")
async def delete_workspace(user=Depends(require_role("owner"))):
    ...

@router.patch("/settings")
async def update_settings(user=Depends(require_role("admin"))):
    ...
```

Workspace roles also participate in [entity permission resolution](permissions.md) -- admins and owners get full access to all resources in the workspace.

## Member Management

### Invite

Admins and owners can invite existing users by email. The user must have logged in at least once via an IdP.

```
POST /workspaces/{workspace_id}/members/invite
{ "email": "alice@example.com", "role": "editor" }
```

### Change Role

```
PATCH /workspaces/{workspace_id}/members/{user_id}
{ "role": "admin" }
```

### Remove

```
DELETE /workspaces/{workspace_id}/members/{user_id}
```

Removing a member invalidates their JWT for that workspace on next token refresh.

## Soft Isolation

All workspaces share the same database and Redis instance. Isolation is enforced at the application layer through `workspace_id` filtering on every query. Users in workspace A cannot see or access resources in workspace B.

Cross-workspace requests are rejected with `403 Forbidden`.
