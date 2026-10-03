import uuid
from datetime import datetime

from pydantic import BaseModel

from src.schemas.validators import SafeStrOptional, SafeUrl


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdateRequest(BaseModel):
    name: SafeStrOptional = None
    avatar_url: SafeUrl = None
