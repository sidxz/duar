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

Per-IP limits enforced via slowapi. Auth endpoints: 10/min (5/min for admin login). Returns `429` when exceeded.

## Sections

- [Authentication](auth.md) -- OAuth login flows, authz mode, token lifecycle
- [Users, Workspaces & Groups](resources.md) -- User profiles, workspace CRUD, group management
- [Permissions](permissions.md) -- Zanzibar-style resource ACLs
- [Roles](roles.md) -- RBAC action registration and checks
- [Schemas](schemas.md) -- Request and response model reference
