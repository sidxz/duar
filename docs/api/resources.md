# Users, Workspaces & Groups

All endpoints in this section require a Bearer JWT in the `Authorization` header.

---

## Users

### GET /users/me

Returns the authenticated user's profile.

**Response:** `200 OK`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "jane@example.com",
  "name": "Jane Doe",
  "avatar_url": "https://example.com/avatar.jpg",
  "is_active": true,
  "created_at": "2025-07-01T14:00:00Z"
}
```

```bash
curl http://localhost:9003/users/me \
  -H "Authorization: Bearer eyJhbGciOi..."
```

### PATCH /users/me

Updates the authenticated user's profile. Both fields are optional.

**Request Body:**

```json
{"name": "Jane Smith", "avatar_url": "https://example.com/new-avatar.jpg"}
```

**Response:** `200 OK` -- updated `UserResponse`.

---

## Workspaces

### Endpoint Table

| Method | Path | Min Role | Description |
|---|---|---|---|
| POST | `/workspaces` | any | Create workspace (caller becomes owner). Requires `SELF_SERVE_ENABLED`; subject to the self-serve cap and hourly breaker (403 / 429). |
| GET | `/workspaces` | any | List user's workspaces |
| GET | `/workspaces/{id}` | any | Get workspace details |
| PATCH | `/workspaces/{id}` | admin | Update workspace |
| DELETE | `/workspaces/{id}` | owner | Delete workspace |
| GET | `/workspaces/{id}/members` | any | List/search members |
| POST | `/workspaces/{id}/members/invite` | admin | Add an existing member (403 while `SELF_SERVE_ENABLED` — use invitations) |
| PATCH | `/workspaces/{id}/members/{user_id}` | admin | Change member role |
| DELETE | `/workspaces/{id}/members/{user_id}` | admin | Remove member |

The JWT's `wid` claim must match the `{id}` path parameter. Cross-workspace access returns `403`.

### POST /workspaces

**Request Body:**

```json
{"name": "Acme Corp", "slug": "acme-corp", "description": "Main workspace"}
```

Slug must match `^[a-z0-9][a-z0-9-]*[a-z0-9]$`.

**Response:** `201 Created` -- `WorkspaceResponse`.

### GET /workspaces

Returns all workspaces the authenticated user belongs to.

**Response:** `200 OK` -- `WorkspaceResponse[]`.

### PATCH /workspaces/{id}

**Request Body:**

```json
{"name": "New Name", "description": "Updated description"}
```

**Response:** `200 OK` -- updated `WorkspaceResponse`.

### DELETE /workspaces/{id}

**Response:** `204 No Content`.

### GET /workspaces/{id}/members

Lists workspace members. Supports optional search and pagination. Rate limited to **60 requests/minute**.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string (optional) | Search by name or email (max 100 characters) |
| `limit` | integer (optional) | Max results to return (1--50) |

**Response:** `200 OK` -- `WorkspaceMemberResponse[]`.

```bash
curl "http://localhost:9003/workspaces/a1b2c3d4-.../members?q=jane&limit=10" \
  -H "Authorization: Bearer eyJhbGciOi..."
```

### POST /workspaces/{id}/members/invite

**Request Body:**

```json
{"email": "bob@example.com", "role": "editor"}
```

Role must be one of: `owner`, `admin`, `editor`, `viewer` (default: `viewer`).

**Response:** `201 Created` -- `WorkspaceMemberResponse`.

### PATCH /workspaces/{id}/members/{user_id}

**Request Body:**

```json
{"role": "admin"}
```

**Response:** `200 OK` -- updated membership.

### DELETE /workspaces/{id}/members/{user_id}

**Response:** `204 No Content`.

---

## Groups

Groups are scoped to a workspace. All routes are under `/workspaces/{workspace_id}/groups`.

### Endpoint Table

| Method | Path | Min Role | Description |
|---|---|---|---|
| POST | `/workspaces/{wid}/groups` | admin | Create group |
| GET | `/workspaces/{wid}/groups` | any | List groups |
| PATCH | `/workspaces/{wid}/groups/{gid}` | admin | Update group |
| DELETE | `/workspaces/{wid}/groups/{gid}` | admin | Delete group |
| POST | `/workspaces/{wid}/groups/{gid}/members/{uid}` | admin | Add member |
| GET | `/workspaces/{wid}/groups/{gid}/members` | any | List group members |
| DELETE | `/workspaces/{wid}/groups/{gid}/members/{uid}` | admin | Remove member |

### POST /workspaces/{workspace_id}/groups

**Request Body:**

```json
{"name": "Engineering", "description": "Engineering team"}
```

**Response:** `201 Created` -- `GroupResponse`.

### PATCH /workspaces/{workspace_id}/groups/{group_id}

**Request Body:**

```json
{"name": "Platform Engineering"}
```

**Response:** `200 OK` -- updated `GroupResponse`.

### GET /workspaces/{workspace_id}/groups/{group_id}/members

Lists members of a group. The group must belong to the specified workspace. Rate limited to **60 requests/minute**.

**Response:** `200 OK` -- `GroupMemberResponse[]`.

**Errors:** `404` group not found in this workspace.

```bash
curl http://localhost:9003/workspaces/a1b2c3d4-.../groups/b2c3d4e5-.../members \
  -H "Authorization: Bearer eyJhbGciOi..."
```

### POST /workspaces/{workspace_id}/groups/{group_id}/members/{user_id}

**Response:** `201 Created`

```json
{"status": "ok"}
```

### DELETE /workspaces/{workspace_id}/groups/{group_id}/members/{user_id}

**Response:** `204 No Content`.
