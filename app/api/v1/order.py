"""订单确认接口（设计文档 §5.1 POST /api/v1/order/confirm）。

下单必须显式 UI 确认：前端确认卡片携带 preview_id 调本接口，
服务端做三重校验（归属 / TTL / 金额时效复核）后提交订单。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import UserContext, get_current_user
from app.core.errors import AppError, ErrorCode
from app.models.audit import AiOrderPreview
from app.models.db import get_db
from app.tools.base import ToolContext, ToolResult
from app.tools.order_tools import order_submit

router = APIRouter()


class ConfirmRequest(BaseModel):
    preview_id: str


@router.post("/order/confirm")
def confirm_order(body: ConfirmRequest, request: Request,
                  user: UserContext = Depends(get_current_user),
                  db: OrmSession = Depends(get_db)):
    preview = db.get(AiOrderPreview, body.preview_id)
    if preview is None or preview.member_id != user.member_id:
        raise AppError(*ErrorCode.PREVIEW_INVALID, http_status=400)

    ctx = ToolContext(
        user_token=request.state.user_token,
        member_id=user.member_id,
        shop_id=user.shop_id,
        session_id=preview.session_id,
    )
    result: ToolResult = _run(order_submit, ctx, body.preview_id)

    from app.observability import metrics as mt
    if not result.ok:
        mt.order_confirmed_total.labels(result="fail").inc()
        raise AppError(result.error_code or "AI_2002",
                       result.user_message or "下单失败",
                       http_status=400, detail=result.data)
    mt.order_confirmed_total.labels(result="ok").inc()
    return {
        "order_id": result.data["order_id"],
        "pay_price": result.data["pay_price"],
        "message": "下单成功，请在收银台完成支付",
    }


def _run(fn, ctx, preview_id) -> ToolResult:
    """同步端点里执行异步工具：订单提交含上游 IO，用独立事件循环。"""
    import asyncio
    return asyncio.run(fn(ctx, preview_id=preview_id))
