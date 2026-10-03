import uuid

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(max_length=4096)


class TokenPayload(BaseModel):
    sub: uuid.UUID
    email: str
    name: str
    wid: uuid.UUID
    wslug: str
    wrole: str
    groups: list[uuid.UUID]


class ProviderListResponse(BaseModel):
    providers: list[str]


class SelectWorkspaceRequest(BaseModel):
    code: str
    workspace_id: uuid.UUID
    code_verifier: str = Field(min_length=43, max_length=128)


class WorkspaceListRequest(BaseModel):
    code: str
    code_verifier: str = Field(min_length=43, max_length=128)


class WorkspaceOptionResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str
