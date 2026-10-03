import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    CurrentUser,
    ServiceKeyContext,
    get_user_for_service_call,
    require_service_key,
    verify_service_scope,
)
from src.database import get_db
from src.schemas.role import (
    CheckActionRequest,
    CheckActionResponse,
    RegisterActionsRequest,
    ServiceActionResponse,
    UserActionsResponse,
)
from src.services import role_service

router = APIRouter(prefix="/roles", tags=["roles"])
logger = structlog.get_logger()


# --- Service-only auth: service key ---


@router.post(
    "/actions/register", response_model=list[ServiceActionResponse], status_code=201
)
async def register_actions(
    body: RegisterActionsRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    verify_service_scope(svc, body.service_name)
    actions = await role_service.register_actions(
        db,
        service_name=body.service_name,
        actions=[
            {"action": a.action, "description": a.description} for a in body.actions
        ],
    )
    return actions


# --- Dual auth: service key + user JWT ---


@router.post("/check-action", response_model=CheckActionResponse)
async def check_action(
    body: CheckActionRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    user: CurrentUser = Depends(get_user_for_service_call),
    db: AsyncSession = Depends(get_db),
):
    verify_service_scope(svc, body.service_name)
    if body.workspace_id != user.workspace_id:
        raise HTTPException(status_code=403, detail="Cross-workspace check not allowed")

    allowed, roles = await role_service.check_action(
        db,
        user_id=user.user_id,
        service_name=body.service_name,
        action=body.action,
        workspace_id=body.workspace_id,
    )
    # Best-effort: a failed usage/denial record must never break the verdict
    # response — this is the SDK require_action hot path.
    try:
        await role_service.record_action_check(
            db,
            allowed=allowed,
            user_id=user.user_id,
            service_name=body.service_name,
            action=body.action,
            workspace_id=body.workspace_id,
        )
        await db.commit()
    except Exception:
        logger.warning(
            "audit.write_failed",
            category="app",
            reason="action_check_record",
            exc_info=True,
        )
        try:
            await db.rollback()
        except Exception:
            pass
    return CheckActionResponse(allowed=allowed, roles=roles)


@router.get("/user-actions", response_model=UserActionsResponse)
async def get_user_actions(
    workspace_id: uuid.UUID = Query(...),
    service_name: str = Query(...),
    svc: ServiceKeyContext = Depends(require_service_key),
    user: CurrentUser = Depends(get_user_for_service_call),
    db: AsyncSession = Depends(get_db),
):
    verify_service_scope(svc, service_name)
    if workspace_id != user.workspace_id:
        raise HTTPException(
            status_code=403, detail="Cross-workspace lookup not allowed"
        )

    actions = await role_service.get_user_actions(
        db,
        user_id=user.user_id,
        service_name=service_name,
        workspace_id=workspace_id,
    )
    return UserActionsResponse(actions=actions)
