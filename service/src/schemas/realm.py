"""Schemas for realm self-discovery (whoami) and no-user m2m token minting."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from src.schemas.validators import REALM_SLUG_PATTERN, NonEmptySafeStrOptional, SafeStr


class RealmInfo(BaseModel):
    slug: str
    name: str


class WhoamiResponse(BaseModel):
    """What a service key resolves to: its own name, the shared ``effective_scope``
    it reads/writes permissions under, and its realm (null when standalone)."""

    service_name: str
    effective_scope: str
    realm: RealmInfo | None = None


class M2MTokenRequest(BaseModel):
    target: str | None = Field(
        default=None,
        description=(
            "Reserved for future per-call audience narrowing — NOT enforced in v1. "
            "When honored, restricts the token to a single target service_name."
        ),
    )


class M2MTokenResponse(BaseModel):
    token: str
    expires_in: int


class RealmCreateRequest(BaseModel):
    name: SafeStr = Field(min_length=1, max_length=255)
    slug: str = Field(pattern=REALM_SLUG_PATTERN, max_length=255)
    # No-user m2m token lifetime (seconds): 30s floor, 1h ceiling.
    m2m_ttl_s: int = Field(default=300, ge=30, le=3600)


class RealmUpdateRequest(BaseModel):
    # Slug is intentionally absent — it keys effective_scope and is immutable.
    name: NonEmptySafeStrOptional = None
    m2m_ttl_s: int | None = Field(default=None, ge=30, le=3600)
    is_active: bool | None = None


class RealmResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    m2m_ttl_s: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RealmMemberResponse(BaseModel):
    id: uuid.UUID
    name: str
    service_name: str
    # True if this member already had grants under its own service_name when it
    # joined — those are NOT visible under the realm scope (v1 has no auto-migrate).
    has_grants: bool
