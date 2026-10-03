from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_current_user_flexible,
)
from src.database import get_db
from src.schemas.user import UserResponse, UserUpdateRequest
from src.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
async def get_me(
    user: CurrentUser = Depends(get_current_user_flexible),
    db: AsyncSession = Depends(get_db),
):
    result = await user_service.get_user_by_id(db, user.user_id)
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    return result


@router.patch("/me", response_model=UserResponse)
async def update_me(
    body: UserUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await user_service.update_user(
        db, user.user_id, name=body.name, avatar_url=body.avatar_url
    )
