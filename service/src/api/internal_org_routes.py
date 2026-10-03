"""Org directory for backend services (service-key only, internal listener).

Client apps use this to render org pickers/labels; org identity itself
arrives in their tokens as oid/oslug/opub claims.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import ServiceKeyContext, require_service_key
from src.database import get_db
from src.schemas.organization import OrgDirectoryEntry
from src.services import organization_service

router = APIRouter(prefix="/organizations", tags=["organizations-internal"])


@router.get("", response_model=list[OrgDirectoryEntry])
async def list_organizations(
    include_disabled: bool = False,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    orgs = await organization_service.list_orgs_for_directory(
        db, include_disabled=include_disabled
    )
    return [
        OrgDirectoryEntry(
            id=o.id, slug=o.slug, name=o.name, is_public=o.is_public, enabled=o.enabled
        )
        for o in orgs
    ]
