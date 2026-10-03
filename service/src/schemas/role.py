import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from src.schemas.validators import SafeStr, SafeStrOptional


class ActionDefinition(BaseModel):
    action: str = Field(pattern=r"^[a-z][a-z0-9_.:-]*$")
    description: SafeStrOptional = None


_SVC_NAME_PATTERN = r"^[a-z][a-z0-9_.-]*$"


class RegisterActionsRequest(BaseModel):
    service_name: str = Field(max_length=255, pattern=_SVC_NAME_PATTERN)
    actions: list[ActionDefinition]


class CheckActionRequest(BaseModel):
    service_name: str = Field(max_length=255, pattern=_SVC_NAME_PATTERN)
    action: str = Field(pattern=r"^[a-z][a-z0-9_.:-]*$")
    workspace_id: uuid.UUID


class RoleCreateRequest(BaseModel):
    name: SafeStr
    description: SafeStrOptional = None


class RoleUpdateRequest(BaseModel):
    name: SafeStrOptional = None
    description: SafeStrOptional = None


class AddRoleActionsRequest(BaseModel):
    service_action_ids: list[uuid.UUID]


class ServiceActionResponse(BaseModel):
    id: uuid.UUID
    service_name: str
    action: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RoleResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    description: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    action_count: int = 0
    member_count: int = 0
    group_count: int = 0


class RoleMemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    assigned_at: datetime
    assigned_by: uuid.UUID | None


class RoleGroupResponse(BaseModel):
    group_id: uuid.UUID
    name: str
    description: str | None
    member_count: int
    assigned_at: datetime
    assigned_by: uuid.UUID | None


class CheckActionResponse(BaseModel):
    allowed: bool
    roles: list[str]


class UserActionsResponse(BaseModel):
    actions: list[str]
