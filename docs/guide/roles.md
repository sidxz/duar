# Custom Roles (RBAC)

Custom roles add fine-grained action-based authorization on top of workspace roles. Define actions your service supports, group them into roles, assign roles to users, and check at runtime.

```python
from duar_auth import Duar

duar = Duar(
    base_url="https://duar.example.com",
    service_name="analytics",
    service_key="sk_...",
    actions=[
        {"action": "reports:export", "description": "Export reports as CSV/PDF"},
        {"action": "reports:view", "description": "View report data"},
        {"action": "dashboards:create", "description": "Create dashboards"},
    ],
)

@router.get("/reports/export")
async def export_report(user=Depends(duar.require_action("reports:export"))):
    return generate_report()
```

## Core Concepts

### Service Actions

Atomic units of authorization. Each action belongs to a `service_name` and has a unique name.

**Naming convention:** `^[a-z][a-z0-9_.:-]*$`

```
reports:export          analytics service
reports:view            analytics service
templates:manage        cms service
billing:invoices.send   billing service
```

Actions are namespaced by `service_name` -- two services can each define a `view` action without collision.

### Roles

Workspace-scoped collections of actions. A role can include actions from multiple services. Created by admins through the admin panel or API.

| Role | Actions |
|------|---------|
| Analyst | `reports:export`, `reports:view`, `dashboards:create` |
| Template Manager | `templates:create`, `templates:edit`, `templates:delete` |
| Billing Admin | `billing:view`, `billing:manage`, `billing:invoices.send` |

### User Assignments

Users are assigned roles within a workspace. A user can have multiple roles. The effective set of allowed actions is the union of all assigned roles.

## Workflow

### 1. Register Actions on Startup

Pass `actions` to the `Duar` constructor. They are registered automatically during the app lifespan (idempotent -- re-registering updates descriptions without creating duplicates).

```python
duar = Duar(
    base_url="https://duar.example.com",
    service_name="analytics",
    service_key="sk_...",
    actions=[
        {"action": "reports:export", "description": "Export reports"},
        {"action": "reports:view"},
    ],
)

app = FastAPI(lifespan=duar.lifespan)
duar.protect(app)
```

Or register manually with the `RoleClient`:

```python
await duar.roles.register_actions([
    {"action": "reports:export", "description": "Export reports"},
])
```

### 2. Create Roles and Assign Actions (Admin)

Through the admin panel or API:

```
POST /admin/workspaces/{workspace_id}/roles
{ "name": "Analyst", "description": "Can view and export reports" }

POST /admin/roles/{role_id}/actions
{ "service_action_ids": ["uuid-of-reports-export", "uuid-of-reports-view"] }
```

### 3. Assign Users or Groups to Roles (Admin)

```
POST /admin/roles/{role_id}/members/{user_id}
POST /admin/roles/{role_id}/groups/{group_id}
```

Assigning a **group** grants the role's actions to every member of the group,
for as long as they are in it. Groups are flat (no nesting) and
workspace-scoped; a role and its groups must belong to the same workspace.
Group-derived grants resolve live: adding someone to the group grants the
actions on their next check, removing them (or deleting the group) revokes.

!!! note "Delegation boundary"
    Binding a group to a role is admin-panel-only, but *group membership* is
    managed by workspace admins/owners. Once a group is bound, workspace
    admins effectively control who holds those actions — they choose who is
    on the team, never what the team can do. This mirrors how group shares
    already work in entity ACLs.

### 4. Check Actions at Runtime

**Option A -- dependency (recommended):**

```python
@router.get("/reports/export")
async def export_report(user=Depends(duar.require_action("reports:export"))):
    # User is guaranteed to have the action. 403 otherwise.
    return generate_report()
```

**Option B -- manual check:**

```python
allowed = await duar.roles.check_action(token, "reports:export", workspace_id)
```

**Option C -- list all user actions:**

```python
actions = await duar.roles.get_user_actions(token, workspace_id)
# ["reports:export", "reports:view"]
```

## Database Schema

Four tables support the RBAC system:

```
service_actions          Registry of valid actions per service
  UNIQUE(service_name, action)

roles                    Custom roles per workspace
  FK workspace_id -> workspaces (CASCADE)
  UNIQUE(workspace_id, name)

role_actions             Links roles to actions (many-to-many)
  FK role_id -> roles (CASCADE)
  FK service_action_id -> service_actions (CASCADE)
  UNIQUE(role_id, service_action_id)

user_roles               User-to-role assignments
  FK user_id -> users (CASCADE)
  FK role_id -> roles (CASCADE)
  UNIQUE(user_id, role_id)
```

The action check is a 4-table join: `user_roles -> roles -> role_actions -> service_actions`, filtered by `user_id`, `workspace_id`, `service_name`, and `action`.

## Key Properties

- **Real-time**: Action checks are live database queries, never cached in JWTs. Revoking a role takes effect immediately.
- **Workspace-scoped**: Roles exist only within a workspace. No global roles.
- **Registered actions only**: Actions must be pre-registered by services before they can be added to roles. This prevents privilege escalation through invented action names.
- **CASCADE delete**: Deleting a workspace removes all its roles and user assignments. Deleting a user removes their role assignments.
