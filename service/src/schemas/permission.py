import uuid
from datetime import datetime

from pydantic import BaseModel, Field


_SVC_NAME_PATTERN = r"^[a-z][a-z0-9_.-]*$"


class PermissionCheckItem(BaseModel):
    service_name: str = Field(max_length=255, pattern=_SVC_NAME_PATTERN)
    resource_type: str = Field(max_length=255, pattern=_SVC_NAME_PATTERN)
    resource_id: uuid.UUID
    action: str = Field(pattern=r"^(view|edit)$")


class PermissionCheckRequest(BaseModel):
    checks: list[PermissionCheckItem] = Field(max_length=100)


class PermissionCheckResult(BaseModel):
    service_name: str
    resource_type: str
    resource_id: uuid.UUID
    action: str
    allowed: bool


class PermissionCheckResponse(BaseModel):
    results: list[PermissionCheckResult]


class RegisterResourceRequest(BaseModel):
    service_name: str = Field(max_length=255, pattern=_SVC_NAME_PATTERN)
    resource_type: str = Field(max_length=255, pattern=_SVC_NAME_PATTERN)
    resource_id: uuid.UUID
    workspace_id: uuid.UUID
    owner_id: uuid.UUID
    visibility: str = Field(default="workspace", pattern=r"^(private|workspace)$")


class UpdateVisibilityRequest(BaseModel):
    visibility: str = Field(pattern=r"^(private|workspace)$")


class ShareRequest(BaseModel):
    grantee_type: str = Field(pattern=r"^(user|group)$")
    grantee_id: uuid.UUID
    permission: str = Field(pattern=r"^(view|edit)$")


class ResourcePermissionResponse(BaseModel):
    id: uuid.UUID
    service_name: str
    resource_type: str
    resource_id: uuid.UUID
    workspace_id: uuid.UUID
    owner_id: uuid.UUID | None
    visibility: str
    created_at: datetime
    shares: list["ResourceShareResponse"]

    model_config = {"from_attributes": True}


class ResourceShareResponse(BaseModel):
    id: uuid.UUID
    grantee_type: str
    grantee_id: uuid.UUID
    permission: str
    granted_by: uuid.UUID | None
    granted_at: datetime

    model_config = {"from_attributes": True}


class EnrichedResourceShareResponse(BaseModel):
    id: uuid.UUID
    grantee_type: str
    grantee_id: uuid.UUID
    grantee_name: str | None = None
    grantee_email: str | None = None
    permission: str
    granted_by: uuid.UUID | None
    granted_by_name: str | None = None
    granted_at: datetime


class EnrichedResourcePermissionResponse(BaseModel):
    id: uuid.UUID
    service_name: str
    resource_type: str
    resource_id: uuid.UUID
    workspace_id: uuid.UUID
    owner_id: uuid.UUID | None
    owner_name: str | None = None
    owner_email: str | None = None
    visibility: str
    created_at: datetime
    shares: list[EnrichedResourceShareResponse]


class AccessibleResourcesRequest(BaseModel):
    service_name: str = Field(max_length=255, pattern=_SVC_NAME_PATTERN)
    resource_type: str = Field(max_length=255, pattern=_SVC_NAME_PATTERN)
    action: str = Field(pattern=r"^(view|edit)$")
    workspace_id: uuid.UUID
    limit: int | None = Field(default=None, ge=1, le=10000)


class AccessibleResourcesResponse(BaseModel):
    resource_ids: list[uuid.UUID]
    has_full_access: bool
