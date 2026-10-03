import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from src.schemas.validators import SafeStr, SafeStrOptional


class GroupCreateRequest(BaseModel):
    name: SafeStr = Field(min_length=1, max_length=255)
    description: SafeStrOptional = None


class GroupUpdateRequest(BaseModel):
    name: SafeStrOptional = Field(default=None, min_length=1, max_length=255)
    description: SafeStrOptional = None


class GroupResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    description: str | None
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    added_at: datetime
