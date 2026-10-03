"""Schemas for AuthZ mode endpoints."""

import uuid

from pydantic import BaseModel, Field


class AuthzResolveRequest(BaseModel):
    idp_token: str = Field(
        description="Raw IdP token (OIDC ID token or OAuth access token)"
    )
    provider: str = Field(description="IdP provider name: google, github, entra_id")
    workspace_id: uuid.UUID | None = Field(
        default=None,
        description="Workspace to authorize for. Omit for workspace list.",
    )
    nonce: str | None = Field(
        default=None,
        description=(
            "Optional replay protection. If provided, must match the "
            "nonce claim on the IdP's ID token (OIDC providers only)."
        ),
    )


class AuthzUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str


class AuthzWorkspaceResponse(BaseModel):
    id: uuid.UUID
    slug: str
    role: str


class AuthzWorkspaceOption(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str


class AuthzResolveResponse(BaseModel):
    user: AuthzUserResponse
    workspace: AuthzWorkspaceResponse | None = None
    authz_token: str | None = None
    expires_in: int | None = None
    workspaces: list[AuthzWorkspaceOption] | None = None
