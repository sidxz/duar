import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from src.schemas.validators import SafeStr, validate_redirect_uri


class ClientAppCreateRequest(BaseModel):
    name: SafeStr
    redirect_uris: list[str]

    @field_validator("redirect_uris", mode="before")
    @classmethod
    def validate_uris(cls, v: list[str]) -> list[str]:
        return [validate_redirect_uri(uri) for uri in v]


class ClientAppUpdateRequest(BaseModel):
    name: SafeStr | None = None
    redirect_uris: list[str] | None = None
    is_active: bool | None = None
    revoke_sessions: bool = False

    @field_validator("redirect_uris", mode="before")
    @classmethod
    def validate_uris(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return [validate_redirect_uri(uri) for uri in v]


class ClientAppResponse(BaseModel):
    id: uuid.UUID
    name: str
    redirect_uris: list[str]
    is_active: bool
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
