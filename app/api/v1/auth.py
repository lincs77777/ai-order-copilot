"""dev 验证端点：/api/v1/me（仅 APP_ENV=dev 注册，用于 S3 验收 token 校验链路）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.security import UserContext

router = APIRouter()


@router.get("/me")
def whoami(request: Request, user: UserContext = Depends(get_current_user)):
    """返回当前 token 解析出的用户上下文（脱敏，仅 dev）。"""
    if get_settings().APP_ENV != "dev":
        raise AppError(*ErrorCode.NOT_FOUND, http_status=404)
    return {
        "member_id": user.member_id,
        "shop_id": user.shop_id,
        "user_type": user.user_type,
        "tenant_id": user.tenant_id,
    }
