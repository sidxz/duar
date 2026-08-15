"""FastAPI dependency helpers for extracting auth context from requests."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx
from fastapi import Depends, HTTPException, Request

from duar_auth.auth import RequestAuth
from duar_auth.types import AuthenticatedUser, DuarError, WorkspaceContext

if TYPE_CHECKING:
    from duar_auth.permissions import PermissionClient
    from duar_auth.roles import RoleClient


def get_token(request: Request) -> str:
    """Return the Duar token for this request.

    Prefers ``request.state.token`` (set by the middlewares — in authz mode the
    Authorization header carries the IdP token, not a Duar-signed one), and
    falls back to the Authorization header when no middleware stored a token.
    """
    token: str | None = getattr(request.state, "token", None)
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return auth.removeprefix("Bearer ")


def get_current_user(request: Request) -> AuthenticatedUser:
    """Extract the authenticated user from request state (set by JWTAuthMiddleware)."""
    user: AuthenticatedUser | None = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_workspace_id(user: AuthenticatedUser = Depends(get_current_user)) -> uuid.UUID:
    """Extract the workspace ID from the current user's JWT context."""
    return user.workspace_id


def get_workspace_context(
    user: AuthenticatedUser = Depends(get_current_user),
) -> WorkspaceContext:
    """Extract full workspace context from the current user's JWT."""
    return WorkspaceContext(
        workspace_id=user.workspace_id,
        workspace_slug=user.workspace_slug,
        user_id=user.user_id,
        role=user.workspace_role,
    )


def require_role(minimum_role: str) -> Callable:
    """Dependency factory that enforces a minimum workspace role.

    Usage:
        @router.post("/things")
        async def create_thing(user: AuthenticatedUser = Depends(require_role("editor"))):
            ...
    """

    def dependency(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if not user.has_role(minimum_role):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions: requires '{minimum_role}' role",
            )
        return user

    return dependency


def require_action(role_client: RoleClient, action: str) -> Callable:
    """Dependency factory that enforces an RBAC action via the identity service.

    Usage:
        @router.get("/reports/export")
        async def export(user: AuthenticatedUser = Depends(require_action(roles, "reports:export"))):
            ...
    """

    async def dependency(request: Request, user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        token = get_token(request)
        try:
            allowed = await role_client.check_action(token, action, user.workspace_id)
        except (httpx.TransportError, httpx.TimeoutException):
            raise HTTPException(status_code=503, detail="Authorization service unavailable")
        except DuarError as exc:
            raise HTTPException(status_code=exc.status_code or 502, detail="Authorization check failed")
        if not allowed:
            raise HTTPException(status_code=403, detail=f"Action '{action}' not permitted")
        return user

    return dependency


def get_request_auth_factory(
    permissions: PermissionClient | None = None,
    roles: RoleClient | None = None,
) -> Callable:
    """Create a FastAPI dependency that returns a ``RequestAuth`` per request.

    The returned dependency extracts the JWT token and authenticated user
    from the request and bundles them into a ``RequestAuth`` object with
    optional permission/role clients wired in.

    Args:
        permissions: Optional ``PermissionClient`` for entity-level checks.
        roles: Optional ``RoleClient`` for RBAC action checks.

    Returns:
        A FastAPI-compatible dependency function.
    """

    def dependency(
        request: Request,
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> RequestAuth:
        return RequestAuth(user=user, _token=get_token(request), _permissions=permissions, _roles=roles)

    return dependency
