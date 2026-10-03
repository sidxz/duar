# Authorization

Duar provides three tiers of authorization, from coarse to fine-grained.

```python
from duar_auth.dependencies import require_role, require_action

# Tier 1 — workspace role from JWT (no API call)
@router.post("/projects")
async def create_project(user=Depends(require_role("editor"))):
    ...

# Tier 2 — RBAC action check (API call to Duar)
@router.get("/reports/export")
async def export_report(user=Depends(duar.require_action("reports:export"))):
    ...

# Tier 3 — entity ACL check (API call to Duar)
@router.get("/documents/{doc_id}")
async def get_document(doc_id: UUID, user=Depends(duar.require_user)):
    if not await duar.permissions.can(token, "document", doc_id, "view"):
        raise HTTPException(403)
    ...
```

## The Three Tiers

### Tier 1: Workspace Roles

Every user has a role in their active workspace: `owner`, `admin`, `editor`, or `viewer`. The role is embedded in the JWT `wrole` claim, so checks are stateless -- no API call needed.

Use for broad access control: "can this user create resources?" or "can this user manage members?"

See [Workspaces](workspaces.md) for the full role hierarchy and permissions matrix.

### Tier 2: Custom RBAC

Services define application-specific actions (e.g. `reports:export`, `templates:manage`) and organize them into named roles. Users are assigned roles within a workspace. Checks hit the Duar API.

Use for action-based authorization: "can this user export reports?" or "can this user approve invoices?"

See [Custom Roles](roles.md) for the full RBAC workflow.

### Tier 3: Entity ACLs

Zanzibar-style per-resource permissions. Resources are registered with Duar using a generic `(service_name, resource_type, resource_id)` tuple. Access is controlled through ownership, visibility, and explicit shares to users or groups.

Use for resource-level authorization: "can user X edit document Y?"

See [Entity Permissions](permissions.md) for the resolution algorithm and share types.

## Which Tier Do I Need?

| Question | Tier | Check |
|----------|------|-------|
| Can this user create resources? | Workspace Role | `require_role("editor")` |
| Can this user manage members? | Workspace Role | `require_role("admin")` |
| Can this user export reports? | Custom RBAC | `duar.require_action("reports:export")` |
| Can this user approve invoices? | Custom RBAC | `duar.require_action("billing:approve")` |
| Can this user view document X? | Entity ACL | `permissions.can(token, "document", doc_id, "view")` |
| Can this user edit project Y? | Entity ACL | `permissions.can(token, "project", proj_id, "edit")` |
| Which documents can this user see? | Entity ACL | `permissions.accessible(token, "document", "view", wid)` |

Most applications use Tier 1 for basic access control and add Tier 2 or Tier 3 as needed. The tiers are independent -- you can use any combination.

```mermaid
flowchart LR
    Request --> T1{Workspace Role}
    T1 -->|"Coarse check<br/>(from JWT)"| T2{Custom RBAC}
    T2 -->|"Action check<br/>(API call)"| T3{Entity ACL}
    T3 -->|"Resource check<br/>(API call)"| Allow([Allow])

    style T1 fill:#2563eb,color:#fff
    style T2 fill:#7c3aed,color:#fff
    style T3 fill:#059669,color:#fff
```

## Groups

[Groups](groups.md) are named collections of users within a workspace. They participate in Tier 3 (entity ACLs) -- sharing a resource with a group grants access to all members. Group IDs are embedded in the JWT for fast permission resolution.

## Realms

A [realm](realms.md) groups service apps into one shared trust boundary: members share sign-in and all three authorization tiers under one **shared scope** (the realm slug substitutes for each member's `service_name`), and can make trusted app-to-app calls with or without a user. Standalone services are unaffected.

## Related

- [Workspaces](workspaces.md) -- workspace roles and member management
- [Custom Roles](roles.md) -- RBAC actions, roles, and assignments
- [Entity Permissions](permissions.md) -- per-resource ACLs and resolution algorithm
- [Groups](groups.md) -- batch permission grants
- [Realms](realms.md) -- shared-scope app groups and app-to-app (m2m) calls
