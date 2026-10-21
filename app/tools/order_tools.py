"""下单闭环工具（设计文档 §4.3 / §5.2）。

- order_preview：会话购物车 → 订单草稿 → Java /app-api/order/compute（权威金额）→ ai_order_preview 落库
- order_submit：仅确认接口可触发（internal_only）——三重校验后调 /app-api/order/create，
  返回 orderId；支付由前端走既有 /app-api/order/pay 收银
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.java_client import get_java_client, resolve_upstream_token
from app.models.audit import AiOrderPreview
from app.models.cart import AiCartItem
from app.models.db import SessionLocal
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec

VALID_ORDER_TYPES = {"takein", "takeout", "desk"}
DEFAULT_GETTIME_MINUTES = 15


def _db() -> OrmSession:
    return SessionLocal()


# ---------- 草稿组装 ----------

def _build_draft_payload(ctx: ToolContext, lines: list[AiCartItem],
                         order_type: str, address_id: str | None,
                         coupon_id: str | None, remark: str) -> dict[str, Any]:
    return {
        "shopId": str(ctx.shop_id or ""),
        "orderType": order_type,
        "addressId": str(address_id or ""),
        "couponId": str(coupon_id or ""),
        "remark": remark or "",
        "payType": "",
        "gettime": DEFAULT_GETTIME_MINUTES,
        "productId": [str(l.product_id) for l in lines],
        "spec": [l.sku_unique for l in lines],
        "number": [str(l.quantity) for l in lines],
    }


async def _call_compute(ctx: ToolContext, payload: dict[str, Any]) -> dict[str, Any]:
    data = await get_java_client().post(
        "/app-api/order/compute",
        token=resolve_upstream_token(ctx.user_token),
        json_body=payload,
    )
    if not isinstance(data, dict) or "payPrice" not in data:
        raise AppError(*ErrorCode.UPSTREAM_ERROR, http_status=502, detail=f"compute 返回异常: {data}")
    return data


# ---------- order_preview ----------

async def order_preview(ctx: ToolContext, order_type: str,
                        address_id: str | None = None, coupon_id: str | None = None,
                        remark: str = "") -> ToolResult:
    if order_type not in VALID_ORDER_TYPES:
        return ToolResult.fail("AI_0001", "取餐方式仅支持 takein（自取）/ takeout（外卖）/ desk（堂食扫码）")

    db = _db()
    try:
        lines = (
            db.query(AiCartItem)
            .filter(AiCartItem.session_id == ctx.session_id, AiCartItem.status == "active")
            .order_by(AiCartItem.id.asc()).all()
        )
        if not lines:
            return ToolResult.fail("AI_0004", "购物车是空的，请先添加商品")
    finally:
        db.close()

    if order_type == "takeout" and not address_id:
        return ToolResult.fail("AI_0001", "外卖需要配送地址，请询问用户的地址ID",
                               data={"missing_fields": ["address_id"]})
    if order_type == "desk":
        return ToolResult.fail("AI_0001", "堂食扫码模式需桌号，当前版本请引导用户使用自取或外卖",
                               data={"missing_fields": ["desk_number"]})

    try:
        payload = _build_draft_payload(ctx, lines, order_type, address_id, coupon_id, remark)
        amounts = await _call_compute(ctx, payload)
    except AppError as e:
        return ToolResult.from_error(e)

    preview_id = str(uuid.uuid4())
    ttl = get_settings().ORDER_PREVIEW_TTL_SECONDS
    now = datetime.now()
    db = _db()
    try:
        db.add(AiOrderPreview(
            preview_id=preview_id, session_id=ctx.session_id, member_id=ctx.member_id,
            shop_id=ctx.shop_id or 0,
            cart_snapshot=payload,
            amount_detail=amounts,
            amount_total=Decimal(str(amounts["payPrice"])),
            status="pending", expires_at=now + timedelta(seconds=ttl), created_at=now,
        ))
        db.commit()
    finally:
        db.close()

    return ToolResult.success(
        {
            "preview_id": preview_id,
            "order_type": order_type,
            "total_price": str(amounts.get("totalPrice")),
            "coupon_price": str(amounts.get("couponPrice")),
            "postage": str(amounts.get("payPostage")),
            "pay_price": str(amounts.get("payPrice")),
            "expires_in_seconds": ttl,
            "hint": "已生成订单确认卡片，等待用户在前端点击'确认提交'；不要自行提交订单",
        },
        preview_id=preview_id, pay_price=str(amounts.get("payPrice")),
    )


# ---------- order_submit（仅确认接口触发） ----------

async def order_submit(ctx: ToolContext, preview_id: str) -> ToolResult:
    db = _db()
    try:
        preview = db.get(AiOrderPreview, preview_id)
        if preview is None or preview.member_id != ctx.member_id:
            return ToolResult.fail(*ErrorCode.PREVIEW_INVALID)
        if preview.status != "pending":
            return ToolResult.fail(*ErrorCode.PREVIEW_INVALID)
        if preview.expires_at <= datetime.now():
            preview.status = "expired"
            db.commit()
            return ToolResult.fail(*ErrorCode.PREVIEW_INVALID)

        # ① 金额时效复核：重调 compute，与快照比对
        try:
            amounts = await _call_compute(ctx, preview.cart_snapshot)
        except AppError as e:
            return ToolResult.from_error(e)
        if Decimal(str(amounts["payPrice"])) != preview.amount_total:
            preview.status = "expired"
            db.commit()
            return ToolResult.fail(
                *ErrorCode.AMOUNT_CHANGED,
                data={"preview_price": str(preview.amount_total),
                      "current_price": str(amounts["payPrice"])})

        # ② 下单
        try:
            created = await get_java_client().post(
                "/app-api/order/create",
                token=resolve_upstream_token(ctx.user_token),
                json_body=preview.cart_snapshot,
            )
        except AppError as e:
            return ToolResult.from_error(e)
        order_id = (created or {}).get("orderId")
        if not order_id:
            return ToolResult.fail("AI_2002", "下单未返回订单号，请联系店员核实",
                                   data=created)

        # ③ 标记 confirmed + 清空会话购物车
        preview.status = "confirmed"
        db.query(AiCartItem).filter(
            AiCartItem.session_id == preview.session_id, AiCartItem.status == "active"
        ).update({"status": "removed", "updated_at": datetime.now()})
        db.commit()
    finally:
        db.close()

    return ToolResult.success(
        {
            "order_id": order_id,
            "pay_price": str(preview.amount_total),
            "pay_hint": "请引导用户在前端收银台完成支付（30 分钟内未支付将自动取消）",
        },
        order_id=order_id,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        name="order_preview",
        description="生成订单预览（结算金额由服务端计算）。用户表达下单意向（'下单/结账/就这些了'）时调用。"
                    "外卖必须先问用户要配送地址ID；堂食需桌号（当前版本引导用自取/外卖）。"
                    "调用后前端会弹出确认卡片，等待用户确认，你不能替用户提交订单。",
        parameters={
            "type": "object",
            "properties": {
                "order_type": {"type": "string", "enum": ["takein", "takeout"],
                                "description": "takein=到店自取, takeout=外卖"},
                "address_id": {"type": "string", "description": "配送地址ID（外卖必填，问用户要）"},
                "coupon_id": {"type": "string", "description": "优惠券ID（可选）"},
                "remark": {"type": "string", "description": "备注（可选）"},
            },
            "required": ["order_type"],
        },
        handler=order_preview,
        allowed_intents={"ordering"},
    ))
    registry.register(ToolSpec(
        name="order_submit",
        description="【内部】提交订单。只能由前端确认接口触发，禁止在对话中调用。",
        parameters={
            "type": "object",
            "properties": {"preview_id": {"type": "string"}},
            "required": ["preview_id"],
        },
        handler=order_submit,
        allowed_intents=set(),
        internal_only=True,
    ))
