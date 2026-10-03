import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from src.schemas.validators import (
    SafeStr,
    SafeStrOptional,
    strip_html_required,
    validate_origin_string,
)


class ServiceAppCreateRequest(BaseModel):
    name: SafeStr = Field(min_length=1, max_length=255)
    service_name: str = Field(
        pattern=r"^[a-z][a-z0-9-]*[a-z0-9]$", min_length=2, max_length=255
    )
    allowed_origins: list[str] = Field(default_factory=list)
    # The app's registered IdP audience(s) — its OIDC client_id(s). Binds an IdP token
    # to this app at /authz/resolve; empty => fall back to the deployment-wide audience.
    allowed_idp_audiences: list[str] = Field(default_factory=list)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def validate_origins(cls, v: list[str]) -> list[str]:
        return [validate_origin_string(o) for o in v]

    @field_validator("allowed_idp_audiences", mode="before")
    @classmethod
    def validate_audiences(cls, v: list[str]) -> list[str]:
        return [strip_html_required(a) for a in v]


class ServiceAppUpdateRequest(BaseModel):
    name: SafeStrOptional = None
    is_active: bool | None = None
    allowed_origins: list[str] | None = None
    allowed_idp_audiences: list[str] | None = None

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def validate_origins(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [validate_origin_string(o) for o in v]

    @field_validator("allowed_idp_audiences", mode="before")
    @classmethod
    def validate_audiences(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [strip_html_required(a) for a in v]


class ServiceAppResponse(BaseModel):
    id: uuid.UUID
    name: str
    service_name: str
    realm_id: uuid.UUID | None = None
    key_prefix: str
    is_active: bool
    allowed_origins: list[str]
    allowed_idp_audiences: list[str]
    last_used_at: datetime | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ServiceAppCreateResponse(ServiceAppResponse):
    api_key: str
