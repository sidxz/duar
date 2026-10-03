# Database Schema

Duar uses PostgreSQL 16 with SQLAlchemy 2.0 async and Alembic for migrations.

---

## Tables

15 tables across five domains:

| Domain | Tables |
|--------|--------|
| **Users** | `users`, `social_accounts` |
| **Workspaces** | `workspaces`, `workspace_memberships`, `groups`, `group_memberships` |
| **Permissions** | `resource_permissions`, `resource_shares` |
| **RBAC** | `service_actions`, `roles`, `role_actions`, `user_roles` |
| **App Registration** | `service_apps`, `client_apps` |
| **System** | `activity_log` |

---

## Entity Relationship Diagram

```mermaid
erDiagram
    users ||--o{ social_accounts : "has"
    users ||--o{ workspace_memberships : "belongs to"
    users ||--o{ group_memberships : "belongs to"
    users ||--o{ resource_permissions : "owns"
    users ||--o{ user_roles : "has"
    workspaces ||--o{ workspace_memberships : "has members"
    workspaces ||--o{ groups : "contains"
    workspaces ||--o{ resource_permissions : "scoped to"
    workspaces ||--o{ roles : "contains"
    groups ||--o{ group_memberships : "has members"
    resource_permissions ||--o{ resource_shares : "shared via"
    roles ||--o{ role_actions : "grants"
    roles ||--o{ user_roles : "assigned via"
    service_actions ||--o{ role_actions : "used by"

    users {
        uuid id PK
        text email UK
        text name
        text avatar_url
        bool is_active
        bool is_admin
        timestamptz created_at
        timestamptz updated_at
    }
    social_accounts {
        uuid id PK
        uuid user_id FK
        text provider
        text provider_user_id
        jsonb provider_data
    }
    workspaces {
        uuid id PK
        text slug UK
        text name
        uuid created_by FK
    }
    workspace_memberships {
        uuid id PK
        uuid workspace_id FK
        uuid user_id FK
        text role
    }
    resource_permissions {
        uuid id PK
        text service_name
        text resource_type
        uuid resource_id
        uuid workspace_id FK
        uuid owner_id FK
        text visibility
    }
    resource_shares {
        uuid id PK
        uuid resource_permission_id FK
        text grantee_type
        uuid grantee_id
        text permission
        uuid granted_by FK
    }
    service_actions {
        uuid id PK
        text service_name
        text action
        text description
    }
    roles {
        uuid id PK
        uuid workspace_id FK
        text name
        text description
    }
    role_actions {
        uuid id PK
        uuid role_id FK
        uuid service_action_id FK
    }
    user_roles {
        uuid id PK
        uuid user_id FK
        uuid role_id FK
        uuid assigned_by FK
    }
    service_apps {
        uuid id PK
        text name
        text service_name UK
        text key_hash UK
        bool is_active
    }
    client_apps {
        uuid id PK
        text name
        text[] redirect_uris
        bool is_active
    }
```

---

## Conventions

- All primary keys are UUID v4, generated client-side
- Timestamps use `DateTime(timezone=True)` with `server_default=func.now()`
- Cascade deletes configured at the database level (`ondelete="CASCADE"`)
- Check constraints enforce valid enum values (roles, visibility, permissions)
- Composite unique constraints prevent duplicate memberships and shares

### Key Constraints

| Constraint | Table | Purpose |
|------------|-------|---------|
| `uq_workspace_member` | `workspace_memberships` | One membership per user per workspace |
| `uq_resource_identity` | `resource_permissions` | One record per service+type+id |
| `uq_resource_share` | `resource_shares` | One share per resource per grantee |
| `uq_service_action` | `service_actions` | One action per service name |
| `uq_workspace_role_name` | `roles` | Unique role names within a workspace |
| `ck_membership_role` | `workspace_memberships` | Role must be owner/admin/editor/viewer |
| `ck_visibility` | `resource_permissions` | Must be private/workspace |
| `ck_share_permission` | `resource_shares` | Must be view/edit |

---

## Migrations

Alembic manages schema changes. Migrations run automatically on service startup (configured in `main.py` lifespan), so manual runs are rarely needed.

### Create a Migration

After modifying a model:

```bash
cd service && uv run alembic revision --autogenerate -m "description of change"
```

Review the generated `upgrade()` and `downgrade()` functions in `service/migrations/versions/` before committing.

### Manual Commands

```bash
cd service && uv run alembic upgrade head     # Apply all pending
cd service && uv run alembic current          # Show current revision
cd service && uv run alembic downgrade -1     # Roll back one step
```
