# Role Endpoints

RBAC action registration and checks. All routes under `/roles`.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/roles/actions/register` | Service key | Register actions for a service |
| POST | `/roles/check-action` | Service key + JWT | Check if user can perform an action |
| GET | `/roles/user-actions` | Service key + JWT | List all actions a user can perform |

Roles are managed via the admin panel. These endpoints are for service-to-service use.

---

## POST /roles/actions/register

Registers actions for a service. Idempotent -- existing actions get descriptions updated, new actions are created.

**Auth:** Service key only.

**Request Body:**

```json
{
  "service_name": "analytics",
  "actions": [
    {"action": "reports:export", "description": "Export reports as CSV/PDF"},
    {"action": "reports:view", "description": "View report data"}
  ]
}
```

Action names must match `^[a-z][a-z0-9_.:-]*$`.

**Response:** `201 Created`

```json
[
  {
    "id": "a1b2c3d4-...",
    "service_name": "analytics",
    "action": "reports:export",
    "description": "Export reports as CSV/PDF",
    "created_at": "2025-07-01T14:00:00Z"
  }
]
```

```bash
curl -X POST http://localhost:9003/roles/actions/register \
  -H "X-Service-Key: sk_your_key" \
  -H "Content-Type: application/json" \
  -d '{"service_name":"analytics","actions":[{"action":"reports:export","description":"Export reports"}]}'
```

---

## POST /roles/check-action

Checks whether the user can perform a specific action in a workspace. Returns the roles that grant the action — whether assigned to the user directly or via a group the user belongs to.

**Auth:** Service key + Bearer JWT.

**Request Body:**

```json
{
  "service_name": "analytics",
  "action": "reports:export",
  "workspace_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

`workspace_id` must match the JWT's workspace claim.

**Response:** `200 OK`

```json
{"allowed": true, "roles": ["Analyst", "Power User"]}
```

**Errors:** `403` cross-workspace check.

```bash
curl -X POST http://localhost:9003/roles/check-action \
  -H "X-Service-Key: sk_your_key" \
  -H "Authorization: Bearer eyJhbGciOi..." \
  -H "Content-Type: application/json" \
  -d '{"service_name":"analytics","action":"reports:export","workspace_id":"a1b2c3d4-..."}'
```

---

## GET /roles/user-actions

Returns all actions the user can perform for a service in a workspace.

**Auth:** Service key + Bearer JWT.

| Parameter | In | Required | Description |
|---|---|---|---|
| `service_name` | query | Yes | Service to query |
| `workspace_id` | query | Yes | Workspace scope (must match JWT) |

**Response:** `200 OK`

```json
{"actions": ["reports:export", "reports:view", "dashboards:create"]}
```

**Errors:** `403` cross-workspace lookup.

```bash
curl "http://localhost:9003/roles/user-actions?service_name=analytics&workspace_id=a1b2c3d4-..." \
  -H "X-Service-Key: sk_your_key" \
  -H "Authorization: Bearer eyJhbGciOi..."
```
