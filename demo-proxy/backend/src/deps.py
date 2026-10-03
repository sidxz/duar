"""Convenience dependencies for the demo app."""

from fastapi import Request

from duar_auth.dependencies import (  # noqa: F401
    get_current_user,
    get_workspace_context,
    get_workspace_id,
    require_role,
)
from duar_auth.types import AuthenticatedUser, WorkspaceContext  # noqa: F401


def get_token(request: Request) -> str:
    """Extract raw JWT from the Authorization header."""
    return request.headers["Authorization"].removeprefix("Bearer ")
