# Admin Panel

The admin panel is a React SPA for managing Duar's configuration. It runs on port 9004.

## Getting Started

```bash
make admin    # starts admin UI on http://localhost:9004
```

On first launch, you are prompted to create an admin account with a username and password. This is the only locally-managed account in Duar — all other users authenticate through external IdPs.

Admin sessions use a secure HTTP-only cookie with a dedicated `sentinel:admin` JWT audience, separate from user tokens.

## Sections

### Dashboard

Overview of system activity: total users, workspaces, active sessions, and recent login events. Quick health check for connected services (PostgreSQL, Redis).

### Users

Lists all users provisioned through IdP logins. View profile details, linked IdP provider, workspace memberships, and activation status. Deactivate a user to immediately revoke all their sessions — active JWTs are rejected at the middleware level.

### Workspaces

Create and manage workspaces. Each workspace has a unique slug, and users are assigned a workspace role (owner, admin, editor, viewer). Manage membership and roles from the workspace detail view.

### Client Apps

Registered OAuth2 redirect URI allowlists for frontend applications. Each client app defines which redirect URIs are permitted during the authentication flow. Duar does not issue client credentials — these entries exist solely to validate redirect targets.

### Service Apps

Backend services that call Duar APIs. Create service apps to generate API keys, configure allowed origins for browser frontends, and monitor last-used timestamps. See [Service Apps](service-apps.md) for details on auth tiers and SDK usage.

### Roles

Define custom RBAC roles scoped to a workspace and service. Each role is a named collection of actions (e.g., `reports:export`, `documents:delete`). Assign roles to users within a workspace. Actions are namespaced by service name and validated against the pattern `^[a-z][a-z0-9_.:-]*$`.

### Permissions

Browse and manage entity-level ACLs (Zanzibar-style). View which users have access to specific resources, inspect permission records, and audit sharing. Resources are identified by `service_name` + `resource_type` + `resource_id`.
