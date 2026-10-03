import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from src.schemas.validators import SLUG_PATTERN, SafeStr, SafeStrOptional


class WorkspaceCreateRequest(BaseModel):
    name: SafeStr = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100, pattern=SLUG_PATTERN)
    description: SafeStrOptional = None


class WorkspaceUpdateRequest(BaseModel):
    name: SafeStrOptional = Field(default=None, min_length=1, max_length=255)
    description: SafeStrOptional = None


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    created_by: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkspaceMemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None
    role: str
    joined_at: datetime


class InviteMemberRequest(BaseModel):
    email: str
    role: str = Field(default="viewer", pattern=r"^(owner|admin|editor|viewer)$")


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(pattern=r"^(owner|admin|editor|viewer)$")
