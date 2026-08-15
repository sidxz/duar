# DDD / Clean Architecture

Integrate Duar into applications that follow Domain-Driven Design, Clean Architecture, or similar layered patterns — where inner layers must remain framework-agnostic.

The key concept is **`RequestAuth`** — a per-request object that bundles authenticated user identity with token-backed authorization methods. It satisfies any `Protocol` your application layer defines via structural typing, so your domain and application layers never import the SDK.

## The Problem

In a layered architecture:

```
Interfaces (FastAPI)  →  Application (Use Cases)  →  Domain (Aggregates)
```

Inner layers cannot depend on FastAPI or HTTP frameworks. But auth integration typically requires threading tokens and HTTP clients through your code.

`RequestAuth` solves this by hiding the token internally and exposing a clean interface that your use cases consume through structural typing.

## RequestAuth

Wraps three things in one object:

| What | How | Network call? |
|------|-----|---------------|
| **Identity** | `user_id`, `workspace_id`, `workspace_role`, `email`, `name`, `groups`, `is_admin`, `is_editor` | No |
| **Role checks** | `has_role(minimum_role)` — local hierarchy check | No |
| **Authorization** | `can()`, `check_action()`, `accessible()`, `register_resource()` — calls Duar API | Yes (deduplicated) |

**Per-request deduplication**: `can()`, `check_action()`, and `accessible()` results are automatically cached within the same `RequestAuth` instance. If multiple code paths call `auth.accessible("document", "view")` during the same request, only one HTTP call is made. Since `RequestAuth` is created fresh per request, there is zero risk of cross-request leakage.

```python
from duar_auth import RequestAuth
```

### Properties

```python
auth.user_id         # uuid.UUID — from JWT sub
auth.workspace_id    # uuid.UUID — from JWT wid
auth.workspace_role  # str — "owner", "admin", "editor", "viewer"
auth.email           # str
auth.name            # str
auth.groups          # list[uuid.UUID]
auth.is_admin        # bool — True if admin or owner
auth.is_editor       # bool — True if editor, admin, or owner
```

### Methods

```python
# Tier 1: workspace role (no network call)
auth.has_role("editor")  # True for editor, admin, owner

# Tier 2: RBAC action check
await auth.check_action("reports:export")  # bool

# Tier 3: entity-level ACL
await auth.can("document", doc_id, "edit")  # bool

# Tier 3: list filtering
ids, has_all = await auth.accessible("document", "view", limit=50)

# Register resource ACL (uses workspace_id + user_id from context)
await auth.register_resource("document", doc_id, visibility="workspace")
```

## Setup (AuthZ Mode)

AuthZ mode is the recommended default. Your app handles IdP login (Google, GitHub, etc.), and Duar validates the IdP token and issues an authorization JWT. The SDK middleware validates both tokens on every request.

### 1. Install

```bash
pip install duar-auth
```

Or in `pyproject.toml`:

```toml
dependencies = ["duar-auth"]
```

### 2. Create Duar instance

```python
# infrastructure/auth.py
from duar_auth import Duar

duar = Duar(
    base_url="http://localhost:9003",
    service_name="my-service",
    service_key="sk_...",
    mode="authz",  # default
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience="123-abc.apps.googleusercontent.com",  # your OAuth client_id
    actions=[
        {"action": "documents:export", "description": "Export documents"},
        {"action": "documents:bulk-delete", "description": "Bulk delete documents"},
    ],
)
```

`idp_jwks_url` points to your IdP's JWKS endpoint. The middleware uses it to verify IdP tokens with automatic key rotation.

### 3. Wire into FastAPI

```python
# interfaces/api/main.py
from fastapi import FastAPI
from your_app.infrastructure.auth import duar

app = FastAPI(lifespan=duar.lifespan)
duar.protect(app, exclude_paths=["/health", "/docs", "/openapi.json"])
```

- `lifespan` fetches Duar's public key on startup and registers RBAC actions
- `protect()` adds `AuthzMiddleware` which validates both IdP and authz tokens on every request

### 4. Expose the dependency

```python
# interfaces/dependencies.py
from your_app.infrastructure.auth import duar

get_auth = duar.get_auth
```

## Application Layer

### Define an AuthContext Protocol

Your application layer defines its own protocol — zero SDK imports:

```python
# application/ports/auth.py
from typing import Protocol
from uuid import UUID


class AuthContext(Protocol):
    @property
    def user_id(self) -> UUID: ...
    @property
    def workspace_id(self) -> UUID: ...
    @property
    def workspace_role(self) -> str: ...
    @property
    def is_admin(self) -> bool: ...
    def has_role(self, minimum_role: str) -> bool: ...
```

`RequestAuth` satisfies this protocol via structural typing — no explicit inheritance needed.

Add async methods when you need Tier 2/3:

```python
class AuthContext(Protocol):
    # ... properties above ...

    # Tier 2: RBAC
    async def check_action(self, action: str) -> bool: ...

    # Tier 3: entity ACLs
    async def can(self, resource_type: str, resource_id: UUID, action: str) -> bool: ...
    async def accessible(self, resource_type: str, action: str,
                         limit: int | None = None) -> tuple[list[UUID], bool]: ...
```

### Use cases accept AuthContext

Pass `auth` as a method parameter — not through DI:

```python
# application/use_cases/create_document.py
from your_app.application.ports.auth import AuthContext
from your_app.application.ports.document_repository import DocumentRepository


class CreateDocumentUseCase:
    def __init__(self, repo: DocumentRepository):
        self._repo = repo

    async def execute(self, request: CreateDocRequest, auth: AuthContext):
        # Tier 1: workspace role check (no network call)
        if not auth.has_role("editor"):
            raise ForbiddenError("Requires editor role")

        doc = Document.create(
            title=request.title,
            workspace_id=auth.workspace_id,
            owner_id=auth.user_id,
        )
        await self._repo.save(doc)
        return doc
```

### Three tiers in use cases

| Use case | Auth check | Tier |
|----------|-----------|------|
| CreateDocument | `auth.has_role("editor")` | 1 |
| ListDocuments | scope by `auth.workspace_id` | 1 |
| GetDocument | scope by `auth.workspace_id` | 1 |
| DeleteDocument | `auth.has_role("editor")` + ownership | 1 |
| ExportDocument | `await auth.check_action("documents:export")` | 2 |
| ViewSharedDoc | `await auth.can("document", id, "view")` | 3 |
| EditSharedDoc | `await auth.can("document", id, "edit")` | 3 |

Start with Tier 1 only. Add Tier 2/3 when features demand it.

## Route Layer

Routes inject `RequestAuth` and pass it to use cases:

```python
# interfaces/api/routes/document_routes.py
from fastapi import APIRouter, Depends
from duar_auth import RequestAuth
from your_app.interfaces.dependencies import get_auth, get_container

router = APIRouter(prefix="/documents")

@router.post("/", status_code=201)
async def create_document(
    request: CreateDocRequest,
    auth: RequestAuth = Depends(get_auth),
    container = Depends(get_container),
):
    use_case = container[CreateDocumentUseCase]
    return await use_case.execute(request=request, auth=auth)
```

The pattern is always the same:

1. Add `auth: RequestAuth = Depends(get_auth)`
2. Pass `auth=auth` to `use_case.execute()`
3. The use case handles authorization internally

## Domain Layer

The domain layer has **zero auth awareness**. It receives `workspace_id` and `owner_id` as plain UUIDs:

```python
# domain/aggregates/document.py
class Document:
    def __init__(self, title: str, workspace_id: UUID, owner_id: UUID):
        self.id = uuid4()
        self.title = title
        self.workspace_id = workspace_id
        self.owner_id = owner_id
```

## ACL Registration

When a resource is created, register it with Duar so the permission system can manage access.

### Option A: In the use case (simple apps)

```python
class CreateDocumentUseCase:
    async def execute(self, request: CreateDocRequest, auth: AuthContext):
        doc = Document.create(...)
        await self._repo.save(doc)

        # Register ACL after persisting
        await auth.register_resource("document", doc.id, visibility="workspace")
        return doc
```

### Option B: In an event handler (event-sourced apps)

For event-sourced architectures, register ACLs in a domain event handler — keeping the use case focused on domain logic:

```python
# infrastructure/event_handlers/permission_handler.py
from duar_auth import PermissionClient

class PermissionEventHandler:
    def __init__(self, permission_client: PermissionClient):
        self._pc = permission_client

    async def on_document_created(self, event):
        await self._pc.register_resource(
            resource_type="document",
            resource_id=event.document_id,
            workspace_id=event.workspace_id,
            owner_id=event.owner_id,
            visibility="workspace",
        )
```

This uses the service key directly (no user token), making it a pure infrastructure concern.

## Read Model Isolation

All read queries must filter by `workspace_id` to enforce tenant isolation:

```python
# For SQL (SQLAlchemy)
stmt = select(Document).where(Document.workspace_id == auth.workspace_id)

# For MongoDB
query = {"workspace_id": str(auth.workspace_id)}

# For vector stores (Qdrant, etc.)
filter = Filter(must=[
    FieldCondition(key="workspace_id", match=MatchValue(value=str(auth.workspace_id)))
])
```

## Testing

### Unit tests — no SDK needed

Create a fake that satisfies your `AuthContext` protocol:

```python
class FakeAuth:
    def __init__(self, role="editor", user_id=None, workspace_id=None):
        self.user_id = user_id or uuid4()
        self.workspace_id = workspace_id or uuid4()
        self.workspace_role = role
        self.is_admin = role in ("admin", "owner")
        self.is_editor = role in ("editor", "admin", "owner")
        self.email = "test@example.com"
        self.name = "Test User"
        self.groups = []

    def has_role(self, minimum_role):
        hierarchy = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
        return hierarchy.get(self.workspace_role, -1) >= hierarchy.get(minimum_role, 99)

    async def check_action(self, action):
        return True

    async def can(self, resource_type, resource_id, action):
        return True

    async def accessible(self, resource_type, action, limit=None):
        return [], True

    async def register_resource(self, resource_type, resource_id, visibility="workspace"):
        return {}
```

Test authorization logic without any network calls:

```python
async def test_create_document_requires_editor():
    auth = FakeAuth(role="viewer")
    use_case = CreateDocumentUseCase(repo=FakeRepo())

    with pytest.raises(ForbiddenError):
        await use_case.execute(request, auth=auth)

async def test_create_document_succeeds_for_editor():
    auth = FakeAuth(role="editor")
    use_case = CreateDocumentUseCase(repo=FakeRepo())

    result = await use_case.execute(request, auth=auth)
    assert result.owner_id == auth.user_id
```

### Integration tests — override FastAPI dependency

```python
from duar_auth.types import AuthenticatedUser

def mock_editor():
    return AuthenticatedUser(
        user_id=uuid4(),
        email="test@example.com",
        name="Test User",
        workspace_id=WORKSPACE_ID,
        workspace_slug="test",
        workspace_role="editor",
    )

app.dependency_overrides[duar.require_user] = mock_editor
```

## Architecture Summary

```
┌─────────────────────────────────────────────────────┐
│  Interfaces (FastAPI)                               │
│  ┌───────────────────────────────────────────────┐  │
│  │ Routes                                        │  │
│  │ auth: RequestAuth = Depends(get_auth)         │  │
│  │ use_case.execute(request, auth=auth)          │  │
│  └───────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│  Application (Use Cases)                            │
│  ┌───────────────────────────────────────────────┐  │
│  │ auth: AuthContext  (Protocol — no SDK import) │  │
│  │ auth.has_role("editor")         → Tier 1      │  │
│  │ await auth.check_action(...)    → Tier 2      │  │
│  │ await auth.can(...)             → Tier 3      │  │
│  └───────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│  Domain (Aggregates)                                │
│  ┌───────────────────────────────────────────────┐  │
│  │ workspace_id: UUID  (plain value)             │  │
│  │ owner_id: UUID      (plain value)             │  │
│  │ Zero auth awareness                           │  │
│  └───────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│  Infrastructure                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │ duar = Duar(mode="authz", ...)        │  │
│  │ AuthzMiddleware validates IdP + authz tokens  │  │
│  │ PermissionClient ↔ Duar API               │  │
│  │ RoleClient ↔ Duar API                     │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

## Design Decisions

- **`RequestAuth` flows as method parameter**, not through DI container. DI containers are often singletons; auth is per-request.
- **Application layer defines its own `AuthContext` Protocol** — structural typing means zero SDK imports in inner layers.
- **Domain layer receives `workspace_id` and `owner_id` as plain UUIDs** — zero auth awareness.
- **Start with Tier 1 only.** Add Tier 2 (RBAC) and Tier 3 (entity ACLs) incrementally when features require it.
- **`register_resource` in event handlers** for event-sourced apps. In simpler apps, calling it in the use case is fine.

---

## Proxy Mode

If you use Duar in proxy mode (Duar handles the full OAuth flow and issues a single JWT), the DDD integration pattern is identical. The only difference is the infrastructure setup.

### Setup differences

```python
# infrastructure/auth.py
duar = Duar(
    base_url="http://localhost:9003",
    service_name="my-service",
    service_key="sk_...",
    mode="proxy",
    # No idp_jwks_url needed — Duar handles IdP validation
    actions=[...],
)
```

In proxy mode:

- `protect()` adds `JWTAuthMiddleware` instead of `AuthzMiddleware`
- The middleware validates a single Duar-issued JWT (`audience: sentinel:access`)
- No IdP token handling on your side — Duar manages the entire OAuth flow

### Everything else stays the same

- `RequestAuth` works identically
- `AuthContext` protocol is unchanged
- Use cases, domain layer, testing — all the same
- `PermissionClient` and `RoleClient` work the same way

The only user-visible difference is how login works: in proxy mode, users authenticate through Duar's OAuth endpoints rather than directly with the IdP.
