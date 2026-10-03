# RoleClient

Async HTTP client for Duar's RBAC role/action API. Handles action registration, permission checks, and user action queries.

Usually accessed via `duar.roles`:

```python
duar = Duar(base_url="...", service_name="my-service", service_key="sk_...")
roles = duar.roles
```

Or create directly:

```python
from duar_auth.roles import RoleClient

roles = RoleClient(
    base_url="http://localhost:9003",
    service_name="my-service",
    service_key="sk_...",
)
```

## `register_actions()`

Register actions for this service. Uses service key only (no user JWT). Typically called once on startup via the `Duar` class `actions` parameter.

```python
async def register_actions(actions: list[dict]) -> dict
```

Each dict has `action` (required) and `description` (optional). Action names must match `^[a-z][a-z0-9_.:-]*$`.

```python
await roles.register_actions([
    {"action": "reports:export", "description": "Export reports as CSV"},
    {"action": "reports:delete"},
    {"action": "settings:manage", "description": "Manage workspace settings"},
])
```

Or pass `actions` to the Duar constructor for automatic registration on startup:

```python
duar = Duar(
    ...,
    actions=[
        {"action": "reports:export", "description": "Export reports"},
    ],
)
```

## `check_action()`

Check if the current user can perform an action. Returns `True` or `False`.

```python
async def check_action(token: str, action: str, workspace_id: UUID) -> bool
```

```python
allowed = await roles.check_action(token, "reports:export", user.workspace_id)
if not allowed:
    raise HTTPException(403, "Not permitted")
```

For route-level enforcement, use `require_action()` or `duar.require_action()` instead of calling this directly. See [FastAPI Dependencies](dependencies.md#require_actionrole_client-action).

## `get_user_actions()`

List all actions the current user can perform in a workspace.

```python
async def get_user_actions(token: str, workspace_id: UUID) -> list[str]
```

```python
actions = await roles.get_user_actions(token, user.workspace_id)
# ["reports:export", "reports:delete", "settings:manage"]
```

Useful for building UI permission flags -- fetch once and use client-side to show/hide controls.

## `close()`

Close the underlying `httpx.AsyncClient`. Called automatically by `Duar.lifespan` on shutdown.

```python
await roles.close()
```

`RoleClient` also supports `async with`:

```python
async with RoleClient(base_url="...", service_name="...", service_key="...") as roles:
    allowed = await roles.check_action(token, "reports:export", workspace_id)
```

## Error Handling

All methods raise `DuarError` on non-2xx responses:

```python
from duar_auth.types import DuarError

try:
    await roles.check_action(token, "reports:export", workspace_id)
except DuarError as e:
    print(e.status_code)
```
