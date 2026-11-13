# API Reference

Base URL: `http://localhost:9003` (development). Set `BASE_URL` in production.

Interactive docs available at `/docs` (Swagger UI) when the service is running.

## Authentication Methods

| Method | Header / Mechanism | Used By |
|---|---|---|
| Bearer JWT | `Authorization: Bearer <access_token>` | User-facing endpoints (users, workspaces, groups) |
| Service Key | `X-Service-Key: <key>` | Service-to-service calls (permissions, roles) |
| Service Key + JWT | Both headers above | Dual-auth endpoints that act on behalf of a user |
| Admin Cookie | `admin_token` HttpOnly cookie | Admin panel backend |

Dual-auth endpoints also accept authz tokens (from `/authz/resolve`) in the `Authorization` header alongside the service key.

## Error Format

All errors return JSON:

```json
{"detail": "error message"}
```

| Code | Meaning |
|---|---|
| `400` | Invalid input or misconfigured provider |
| `401` | Missing or invalid credentials |
| `403` | Insufficient role or workspace mismatch |
| `404` | Resource not found |
| `429` | Rate limit exceeded |

## Rate Limits

Every API route except `GET /health` is rate limited. Exceeding a limit returns `429` with a `Retry-After` header (seconds). Defaults:

| Routes | Limit | Counted per |
|---|---|---|
| Sign-in (`/auth/login`, `/auth/callback`, `/auth/token`, `/auth/refresh`, `/authz/idp/...`) | 10/min (5/min for admin sign-in) | client address |
| `POST /authz/resolve` | 60/min, and 600/min for the service overall | calling service + client address |
| Realm tokens (`GET /realm/whoami`, `POST /realm/m2m-token`) | 60/min | calling service |
| Checks and lookups made for a user (`/permissions/check`, `/permissions/accessible`, `/permissions/{id}/share`, `/roles/check-action`, `/roles/user-actions`) | 600/min | calling app + user |
| Service-key routes without a user (`/permissions/register`, visibility, share removal, resource ACL reads and delete, `/roles/actions/register`, `/organizations`) | 3000/min | calling service |
| Directory reads (`/users/me`, workspace members, groups, group members) | 60/min | user |
| Sensitive admin operations (key rotation, bulk status, CSV import, purge) | 5–10/min | admin |
| Everything else | 120/min per route, 300/min across routes | client address |

A limit counts every request to a route together, whatever the values in its path. A client address that keeps failing authentication is answered `429` in place of `401`. Every value is configurable: see [Rate Limiting](../security.md#rate-limiting).

## Sections

- [Authentication](auth.md) -- OAuth login flows, authz mode, token lifecycle
- [Users, Workspaces & Groups](resources.md) -- User profiles, workspace CRUD, group management
- [Permissions](permissions.md) -- Zanzibar-style resource ACLs
- [Roles](roles.md) -- RBAC action registration and checks
- [Schemas](schemas.md) -- Request and response model reference
