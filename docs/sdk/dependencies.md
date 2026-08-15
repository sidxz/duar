# FastAPI Dependencies

All dependencies are in `duar_auth.dependencies`. They extract auth context set by the middleware and inject it into route handlers.

```python
from duar_auth.dependencies import (
    get_current_user,
    get_workspace_id,
    get_workspace_context,
    require_role,
    require_action,
)
```

## `get_current_user`

Returns the `AuthenticatedUser` from `request.state.user`. Raises 401 if not set.

```python
@router.get("/me")
async def me(user: AuthenticatedUser = Depends(get_current_user)):
    return {"email": user.email, "workspace_role": user.workspace_role}
```

## `require_role(minimum_role)`

Dependency factory enforcing a minimum workspace role. Returns `AuthenticatedUser` or raises 403.

Role hierarchy: `viewer < editor < admin < owner`.

```python
@router.post("/documents")
async def create(user: AuthenticatedUser = Depends(require_role("editor"))):
    ...

@router.delete("/workspace")
async def delete_ws(user: AuthenticatedUser = Depends(require_role("owner"))):
    ...
```

403 response: `{"detail": "Insufficient permissions: requires 'admin' role"}`.

## `require_action(role_client, action)`

Dependency factory enforcing an RBAC action via `RoleClient`. Extracts the JWT from the `Authorization` header, calls `role_client.check_action()`, and raises 403 if denied.

```python
from duar_auth.roles import RoleClient

roles = RoleClient(base_url="http://duar:9003", service_name="analytics", service_key="sk_...")

@router.get("/reports/export")
async def export(user: AuthenticatedUser = Depends(require_action(roles, "reports:export"))):
    return generate_report(user.workspace_id)
```

If using the `Duar` class, use `duar.require_action()` instead:

```python
@router.get("/reports/export")
async def export(user: AuthenticatedUser = Depends(duar.require_action("reports:export"))):
    ...
```

Error responses:

| Status | Detail |
|--------|--------|
| 401 | `Missing bearer token` |
| 403 | `Action 'reports:export' not permitted` |
| 503 | `Authorization service unavailable` |

## `get_workspace_id`

Returns the workspace `UUID` from the current user. Depends on `get_current_user` internally.

```python
@router.get("/documents")
async def list_docs(workspace_id: UUID = Depends(get_workspace_id)):
    return await db.query(Document).filter_by(workspace_id=workspace_id).all()
```

## `get_workspace_context`

Returns a `WorkspaceContext` dataclass with workspace-scoped fields.

```python
@router.post("/documents")
async def create(body: CreateDoc, ctx: WorkspaceContext = Depends(get_workspace_context)):
    doc = Document(workspace_id=ctx.workspace_id, owner_id=ctx.user_id)
    ...
```

`WorkspaceContext` fields: `workspace_id` (UUID), `workspace_slug` (str), `user_id` (UUID), `role` (str).

## AuthenticatedUser

Frozen dataclass populated by middleware from JWT claims.

| Field | Type | Source Claim |
|-------|------|-------------|
| `user_id` | `UUID` | `sub` |
| `email` | `str` | `email` |
| `name` | `str` | `name` |
| `workspace_id` | `UUID` | `wid` |
| `workspace_slug` | `str` | `wslug` |
| `workspace_role` | `str` | `wrole` |
| `groups` | `list[UUID]` | `groups` |

### Properties and Methods

| Member | Returns | Description |
|--------|---------|-------------|
| `is_admin` | `bool` | `workspace_role` is `admin` or `owner` |
| `is_editor` | `bool` | `workspace_role` is `editor`, `admin`, or `owner` |
| `has_role(min)` | `bool` | True if role >= `min` in hierarchy `viewer < editor < admin < owner` |

```python
if user.is_admin:
    ...

if user.has_role("editor"):
    ...
```
