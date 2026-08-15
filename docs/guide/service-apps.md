# Service Apps

A **service app** is a registered backend service that calls Duar APIs. Each service app has an API key for server-to-server authentication and an optional list of allowed origins for browser frontends.

A service app can optionally belong to a [realm](realms.md) — a group of apps that share sign-in and permissions under one scope. By default an app is standalone and isolated by its `service_name`.

## Creating a Service App

1. Open the admin panel at `http://localhost:9004`.
2. Go to **Service Apps** and click **Create**.
3. Fill in a display **name** and a unique **service name** (e.g., `docu-store`).
4. Copy the generated API key — it is shown once and cannot be retrieved later.

The service name scopes all permissions and RBAC actions. A key for `docu-store` cannot access resources registered under `billing`.

## Authentication Tiers

Duar endpoints use three auth tiers depending on the trust level required:

| Tier | Headers | Used by | Example endpoints |
|------|---------|---------|-------------------|
| **Service Key + User JWT** (dual) | `X-Service-Key` + `Authorization: Bearer <jwt>` | Backend proxying a user request | `/permissions/check`, `/permissions/accessible`, `/permissions/{id}/share` |
| **Service Key only** | `X-Service-Key` | Backend acting on its own behalf | `/permissions/register`, `/permissions/visibility`, `/roles/check` |
| **Origin-based** | `Origin` (sent by browser) | Browser frontends | `/authz/resolve` |

### Service Key + User JWT (dual auth)

The backend sends both its service key and the user's JWT. Duar verifies the service identity and extracts user context. Used for any operation that is "service X asks on behalf of user Y."

```
POST /permissions/check
X-Service-Key: sk_live_abc123...
Authorization: Bearer eyJhbG...
```

### Service Key only

The backend sends its service key without a user JWT. Used for privileged operations like registering resources or updating visibility.

```
POST /permissions/register
X-Service-Key: sk_live_abc123...
```

### Origin-based

Browser frontends do not have access to service keys. Instead, Duar matches the request's `Origin` header against the `allowed_origins` list on the service app. This is lower trust than a service key — only certain endpoints accept it.

```
POST /authz/resolve
Origin: https://app.example.com
```

Configure allowed origins in the admin panel under the service app's settings.

## Python SDK Usage

Pass the service key when initializing SDK clients:

```python
from duar_auth import PermissionClient

permissions = PermissionClient(
    base_url="http://localhost:9003",
    service_name="docu-store",
    service_key="sk_live_abc123...",
)

# Dual auth — pass user's JWT for check/accessible
allowed = await permissions.check(
    resource_type="document",
    resource_id=doc_id,
    action="edit",
    token=user_jwt,
)

# Service key only — no token needed for register
await permissions.register(
    resource_type="document",
    resource_id=doc_id,
    owner_id=user_id,
    workspace_id=workspace_id,
)
```

## Service App Model

Each service app record stores:

| Field | Purpose |
|-------|---------|
| `name` | Human-readable display name |
| `service_name` | Unique identifier, scopes permissions and roles |
| `key_hash` | SHA-256 hash of the API key |
| `key_prefix` | First 12 chars of the key, shown in admin panel for identification |
| `allowed_origins` | Array of origins for browser frontend auth |
| `is_active` | Disable without deleting |
| `last_used_at` | Updated on each API call |

## Key Rotation

To rotate a service key:

1. Create a new service app with the same `service_name` — this is not currently supported as service names are unique.
2. Instead, regenerate the key in the admin panel, update your backend config, and deploy.

There is no grace period. The old key stops working immediately.
