"""Types representing authenticated user and workspace context from JWT claims.

These dataclasses are populated by the JWT middleware and made available
through FastAPI dependency injection.
"""

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthenticatedUser:
    """Represents an authenticated user extracted from a JWT access token.

    This immutable dataclass is set on ``request.state.user`` by
    ``JWTAuthMiddleware`` and retrieved via ``get_current_user()``.

    Attributes:
        user_id: The user's unique identifier (from JWT ``sub`` claim).
        email: The user's email address.
        name: The user's display name.
        workspace_id: The active workspace ID (from JWT ``wid`` claim).
        workspace_slug: The active workspace's URL slug (from JWT ``wslug`` claim).
        workspace_role: The user's role in the active workspace —
            one of ``'owner'``, ``'admin'``, ``'editor'``, or ``'viewer'``.
        groups: List of group UUIDs the user belongs to in the active workspace.
        org_id: The organization ID that owns the active workspace, if any
            (from JWT ``oid`` claim).
        org_slug: The organization's URL slug (from JWT ``oslug`` claim).
        org_is_public: Whether the organization is public (from JWT ``opub`` claim).

    Example:
        ```python
        from duar_auth.dependencies import get_current_user
        from duar_auth.types import AuthenticatedUser

        @router.get("/items")
        async def list_items(user: AuthenticatedUser = Depends(get_current_user)):
            if user.is_admin:
                return await get_all_items(user.workspace_id)
            return await get_user_items(user.user_id)
        ```
    """

    user_id: uuid.UUID
    email: str
    name: str
    workspace_id: uuid.UUID
    workspace_slug: str
    workspace_role: str
    groups: list[uuid.UUID] = field(default_factory=list)
    org_id: uuid.UUID | None = None
    org_slug: str | None = None
    org_is_public: bool = False

    @property
    def is_admin(self) -> bool:
        """Whether the user has admin or owner role in the active workspace."""
        return self.workspace_role in ("admin", "owner")

    @property
    def is_editor(self) -> bool:
        """Whether the user has at least editor role (editor, admin, or owner)."""
        return self.workspace_role in ("editor", "admin", "owner")

    def has_role(self, minimum_role: str) -> bool:
        """Check if the user meets a minimum role requirement.

        Args:
            minimum_role: The minimum required role — one of
                ``'viewer'``, ``'editor'``, ``'admin'``, or ``'owner'``.

        Returns:
            True if the user's workspace role is equal to or higher than
            the specified minimum in the hierarchy:
            ``viewer < editor < admin < owner``.
        """
        hierarchy = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
        return hierarchy.get(self.workspace_role, -1) >= hierarchy.get(minimum_role, 99)


@dataclass(frozen=True)
class WorkspaceContext:
    """Lightweight workspace context extracted from the current user's JWT.

    A subset of ``AuthenticatedUser`` focused on workspace identity,
    useful when you only need workspace-scoped information.

    Attributes:
        workspace_id: The active workspace's unique identifier.
        workspace_slug: The active workspace's URL-friendly slug.
        user_id: The authenticated user's unique identifier.
        role: The user's role in this workspace.
    """

    workspace_id: uuid.UUID
    workspace_slug: str
    user_id: uuid.UUID
    role: str


class DuarError(Exception):
    """Raised when the Duar identity service returns an error or is unreachable."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)
