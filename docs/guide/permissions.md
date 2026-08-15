# Entity Permissions (ACLs)

Per-resource access control: "can user X view document Y?" Resources are registered with Duar using a generic model. Access is resolved through ownership, visibility, and explicit shares.

```python
# Register a resource when it's created
await duar.permissions.register_resource(
    resource_type="document",
    resource_id=doc.id,
    workspace_id=user.workspace_id,
    owner_id=user.user_id,
    visibility="workspace",
)

# Check access
allowed = await duar.permissions.can(token, "document", doc.id, "edit")

# Share with another user
await duar.permissions.share(
    token=token,
    resource_type="document",
    resource_id=doc.id,
    grantee_type="user",
    grantee_id=collaborator_id,
    permission="edit",
)
```

## Resource Model

Every resource is identified by three fields, making the system generic across services:

| Field | Type | Example |
|-------|------|---------|
| `service_name` | string | `"docu-store"` |
| `resource_type` | string | `"document"` |
| `resource_id` | UUID | `"a1b2c3d4-..."` |

Additional fields on registration:

| Field | Type | Description |
|-------|------|-------------|
| `workspace_id` | UUID | The workspace this resource belongs to |
| `owner_id` | UUID | The user who owns the resource |
| `visibility` | string | `"private"` or `"workspace"` (default: `"workspace"`) |

The `(service_name, resource_type, resource_id)` tuple is unique. Registration is idempotent (upsert with `ON CONFLICT DO NOTHING`).

## Visibility

| Mode | Behavior |
|------|----------|
| `private` | Only the owner and users/groups with explicit shares can access |
| `workspace` | All workspace members can view; editors can also edit |

Change visibility after registration:

```
PATCH /permissions/{permission_id}/visibility
X-Service-Key: your-service-key
{ "visibility": "private" }
```

## Permission Resolution

The `check_permission` function follows a 7-step algorithm, short-circuiting at the first match:

```mermaid
flowchart TD
    Start([check_permission]) --> S1

    S1{1. Resource registered?}
    S1 -->|No| D1[DENY]
    S1 -->|Yes| S2

    S2{2. Same workspace?}
    S2 -->|No| D2[DENY]
    S2 -->|Yes| S3

    S3{3. Is owner?}
    S3 -->|Yes| A1[ALLOW]
    S3 -->|No| S4

    S4{4. Admin or owner role?}
    S4 -->|Yes| A2[ALLOW]
    S4 -->|No| S5

    S5{5. Workspace-visible?}
    S5 -->|No| S6
    S5 -->|Yes| S5a{view?}

    S5a -->|Yes| A3[ALLOW]
    S5a -->|No| S5b{edit + editor role?}

    S5b -->|Yes| A4[ALLOW]
    S5b -->|No| S6

    S6{6. User share?}
    S6 -->|view share + view action| A5[ALLOW]
    S6 -->|edit share + edit action| A6[ALLOW]
    S6 -->|No match| S7

    S7{7. Group share?}
    S7 -->|view share + view action| A7[ALLOW]
    S7 -->|edit share + edit action| A8[ALLOW]
    S7 -->|No match| D3[DENY]

    style D1 fill:#f44,color:#fff
    style D2 fill:#f44,color:#fff
    style D3 fill:#f44,color:#fff
    style A1 fill:#4a4,color:#fff
    style A2 fill:#4a4,color:#fff
    style A3 fill:#4a4,color:#fff
    style A4 fill:#4a4,color:#fff
    style A5 fill:#4a4,color:#fff
    style A6 fill:#4a4,color:#fff
    style A7 fill:#4a4,color:#fff
    style A8 fill:#4a4,color:#fff
```

### Step by Step

1. **Resource registered?** Look up by `(service_name, resource_type, resource_id)`. Not found = deny.
2. **Same workspace?** The resource's `workspace_id` must match the user's JWT `wid`. Cross-workspace = deny.
3. **Owner?** If `owner_id == user_id`, full access (view and edit).
4. **Admin/owner role?** Workspace admins and owners have full access to all resources in the workspace.
5. **Workspace-visible?** If `visibility = "workspace"`: all members can view; users with `editor` role can also edit. Viewers cannot edit unless they have an explicit share.
6. **User share?** Check `resource_shares` for `grantee_type = "user"`, `grantee_id = user_id`. A `view` share grants view; an `edit` share grants both view and edit.
7. **Group share?** Check `resource_shares` for `grantee_type = "group"`, `grantee_id IN (user's group IDs from JWT)`. Same permission logic as user shares.
8. **Default deny.**

## Share Types

Shares grant access to individual users or [groups](groups.md):

| `grantee_type` | `permission` | Effect |
|---|---|---|
| `user` | `view` | User can view the resource |
| `user` | `edit` | User can view and edit the resource |
| `group` | `view` | All group members can view |
| `group` | `edit` | All group members can view and edit |

### Creating Shares

The grantee must belong to the same workspace as the resource. Sharing with a user who isn't a workspace member or a group from another workspace returns `400 Bad Request`.

```python
# Share with a user (must be a workspace member)
await duar.permissions.share(
    token=token,
    resource_type="document",
    resource_id=doc_id,
    grantee_type="user",
    grantee_id=user_id,
    permission="edit",
)

# Share with a group
await duar.permissions.share(
    token=token,
    resource_type="document",
    resource_id=doc_id,
    grantee_type="group",
    grantee_id=group_id,
    permission="view",
)
```

### Revoking Shares

```
DELETE /permissions/{permission_id}/share
X-Service-Key: your-service-key
{ "grantee_type": "user", "grantee_id": "user-uuid" }
```

## Accessible Resources Lookup

List all resource IDs a user can access, useful for filtered list views:

```python
resource_ids, has_full_access = await duar.permissions.accessible(
    token=token,
    resource_type="document",
    action="view",
    workspace_id=workspace_id,
)

if has_full_access:
    # Admin/owner — skip filtering, show everything
    docs = await get_all_docs(workspace_id)
else:
    docs = await get_docs_by_ids(resource_ids)
```

When `has_full_access` is `True` (admin/owner with no limit), `resource_ids` may be empty -- the caller should skip ID filtering entirely.

## Database Schema

```
resource_permissions              One row per registered resource
  UNIQUE(service_name, resource_type, resource_id)
  FK workspace_id -> workspaces (CASCADE)
  FK owner_id -> users (SET NULL)
  CHECK visibility IN ('private', 'workspace')

resource_shares                   Grants on resources
  FK resource_permission_id -> resource_permissions (CASCADE)
  UNIQUE(resource_permission_id, grantee_type, grantee_id)
  CHECK grantee_type IN ('user', 'group')
  CHECK permission IN ('view', 'edit')
```
