# PermissionClient

Async HTTP client for Duar's Zanzibar-style entity permission API. Handles resource registration, permission checks, sharing, and accessible-resource lookups.

Usually accessed via `duar.permissions` (lazily created with the correct `base_url`, `service_name`, and `service_key`):

```python
duar = Duar(base_url="...", service_name="my-service", service_key="sk_...")
perms = duar.permissions
```

Or create directly:

```python
from duar_auth.permissions import PermissionClient

perms = PermissionClient(
    base_url="http://localhost:9003",
    service_name="my-service",
    service_key="sk_...",
    cache_ttl=60,  # optional: cache accessible()/can() results for 60 seconds
)
```

## Response Caching

`PermissionClient` supports opt-in TTL caching for `accessible()` and `can()` results. This eliminates redundant HTTP calls when the same permission is checked multiple times within the TTL window (e.g. across search, browse, and list endpoints in a single user session).

```python
# Via Duar constructor (recommended)
duar = Duar(base_url="...", service_name="...", service_key="...", cache_ttl=120)

# Or directly on PermissionClient
perms = PermissionClient(base_url="...", service_name="...", service_key="...", cache_ttl=120)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `cache_ttl` | `0` | Seconds to cache results. `0` disables caching entirely (default, backward compatible). |

**Cache keys** include a truncated hash of the JWT token, so different users never share cached results.

**Automatic invalidation**: write operations (`share()`, `unshare()`, `update_visibility()`, `register_resource()`, `deregister_resource()`) automatically clear the cache, so subsequent reads reflect the change immediately.

**Staleness window**: if permissions are changed externally (e.g. admin panel, another service instance), cached results may be stale for up to `cache_ttl` seconds. This is acceptable for most workspace-scoped applications where permission changes are infrequent.

## `register_resource()`

Register a new resource ACL. Uses service key only (no user JWT needed).

```python
async def register_resource(
    resource_type: str,
    resource_id: UUID,
    workspace_id: UUID,
    owner_id: UUID,
    visibility: str = "workspace",
) -> dict
```

| Parameter | Description |
|-----------|-------------|
| `resource_type` | Resource type (e.g. `"document"`, `"project"`) |
| `resource_id` | Unique ID of the resource |
| `workspace_id` | Workspace the resource belongs to |
| `owner_id` | User ID of the resource owner |
| `visibility` | `"workspace"` (default), `"private"`, or `"public"` |

```python
await perms.register_resource(
    resource_type="document",
    resource_id=doc.id,
    workspace_id=user.workspace_id,
    owner_id=user.user_id,
    visibility="workspace",
)
```

## `can()`

Check a single permission. Returns `True` or `False`.

```python
async def can(
    token: str,
    resource_type: str,
    resource_id: UUID,
    action: str,
) -> bool
```

The `token` is the user's JWT (or authz token in authz mode). The `service_name` is set on the client.

```python
from duar_auth.dependencies import get_token

@app.get("/documents/{doc_id}")
async def get_doc(doc_id: UUID, token: str = Depends(get_token)):
    if not await perms.can(token, "document", doc_id, "view"):
        raise HTTPException(403, "Access denied")
    return await fetch_document(doc_id)
```

## `check()`

Batch check multiple permissions in one request. Each check can target a different resource type, ID, and action.

```python
async def check(
    token: str,
    checks: list[PermissionCheck],
) -> list[PermissionResult]
```

`PermissionCheck` and `PermissionResult` are dataclasses:

```python
from duar_auth.permissions import PermissionCheck, PermissionResult

results = await perms.check(token, [
    PermissionCheck(service_name="my-service", resource_type="document", resource_id=id1, action="view"),
    PermissionCheck(service_name="my-service", resource_type="document", resource_id=id2, action="edit"),
])

for r in results:
    print(f"{r.resource_id} {r.action}: {r.allowed}")
```

`PermissionResult` fields: `service_name`, `resource_type`, `resource_id` (UUID), `action`, `allowed` (bool).

## `accessible()`

List resource IDs the user can access. Use this to filter query results.

```python
async def accessible(
    token: str,
    resource_type: str,
    action: str,
    workspace_id: UUID,
    limit: int | None = None,
) -> tuple[list[UUID], bool]
```

Returns `(resource_ids, has_full_access)`. When `has_full_access` is `True`, the user can access all resources of this type in the workspace -- skip filtering entirely.

```python
ids, full_access = await perms.accessible(
    token, "document", "view", user.workspace_id,
)

if full_access:
    docs = await get_all_documents(user.workspace_id)
else:
    docs = await get_documents_by_ids(ids)
```

## `share()`

Share a resource with a user or group.

```python
async def share(
    token: str,
    resource_type: str,
    resource_id: UUID,
    grantee_type: str,
    grantee_id: UUID,
    permission: str = "view",
) -> dict
```

| Parameter | Description |
|-----------|-------------|
| `grantee_type` | `"user"` or `"group"` |
| `grantee_id` | UUID of the user or group |
| `permission` | `"view"` (default) or `"edit"` |

```python
await perms.share(
    token=token,
    resource_type="document",
    resource_id=doc_id,
    grantee_type="user",
    grantee_id=collaborator_id,
    permission="edit",
)
```

Internally, `share()` first resolves the resource coordinates to a permission ID, then creates the ACL entry.

## `close()`

Close the underlying `httpx.AsyncClient`. Called automatically by `Duar.lifespan` on shutdown.

```python
await perms.close()
```

`PermissionClient` also supports `async with`:

```python
async with PermissionClient(base_url="...", service_name="...", service_key="...") as perms:
    allowed = await perms.can(token, "document", doc_id, "view")
```

## Error Handling

All methods raise `DuarError` on non-2xx responses from Duar. The error includes the HTTP status code:

```python
from duar_auth.types import DuarError

try:
    await perms.can(token, "document", doc_id, "view")
except DuarError as e:
    print(e.status_code)  # e.g. 404, 502
```
