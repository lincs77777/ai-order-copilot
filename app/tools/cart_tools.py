"""会话购物车工具（设计文档 §5.2 / §6 ai_cart_item）。

yshop 无购物车服务，真源在 ai-service：
- 写操作前实时调 Java 商品详情校验：存在 / 在售 / SKU 有效 / 库存充足
- 记录 price_cached（展示参考）+ verified_at；超过 STALE_SECONDS 触发重校验
- 同 product_id + sku_unique 合并加购；数量 1–20
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session as OrmSession

from app.core.errors import AppError, ErrorCode
from app.models.cart import AiCartItem
from app.models.db import SessionLocal
from app.tools import menu_tools
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec

MAX_QTY = 20
STALE_SECONDS = 300


# ---------- 数据访问 ----------

def _db() -> OrmSession:
    return SessionLocal()


def _active_lines(db: OrmSession, session_id: str) -> list[AiCartItem]:
    return (
        db.query(AiCartItem)
        .filter(AiCartItem.session_id == session_id, AiCartItem.status == "active")
        .order_by(AiCartItem.id.asc())
        .all()
    )


def cart_snapshot_text(db: OrmSession, session_id: str) -> str:
    """给系统提示词注入的购物车快照（纯 DB 读，不做上游重校验）。"""
    if not session_id:
        return "（空）"
    lines = _active_lines(db, session_id)
    if not lines:
        return "（空）"
    rows = [f"- {l.product_name}（{l.sku_unique}）x{l.quantity}" for l in lines]
    return "\n".join(rows)


# ---------- 实时校验 ----------

async def _validated_line(ctx: ToolContext, product_id: int, sku_unique: str, quantity: int) -> dict:
    """写前实时校验。失败抛 AppError（AI_2xxx）。"""
    index = await menu_tools._fetch_product_index(ctx)
    meta = index.get(product_id)
    if meta is None:
        raise AppError(*ErrorCode.PRODUCT_NOT_FOUND, http_status=400)
    if meta.get("is_show") != 1:
        raise AppError(*ErrorCode.PRODUCT_OFF_SHELF, http_status=400)

    detail = await get_java_detail(ctx, product_id)
    sku = next((s for s in detail["skus"] if s["sku_unique"] == sku_unique), None)
    if sku is None:
        raise AppError(*ErrorCode.SKU_NOT_FOUND, http_status=400,
                       detail={"available": [s["sku_unique"] for s in detail["skus"]]})
    if int(sku["stock"] or 0) < quantity:
        raise AppError(*ErrorCode.STOCK_NOT_ENOUGH, http_status=400,
                       detail={"stock": sku["stock"], "requested": quantity})
    return {
        "product_id": product_id,
        "sku_unique": sku_unique,
        "product_name": detail.get("store_name") or meta.get("store_name") or "",
        "spec_text": sku.get("spec"),
        "price_cached": Decimal(str(sku["price"])),
        "stock": sku["stock"],
    }


async def get_java_detail(ctx: ToolContext, product_id: int) -> dict:
    """复用 menu_tools 的详情逻辑（内部含门店归属校验）。"""
    res = await menu_tools.get_dish_detail(ctx, product_id)
    if not res.ok:
        raise AppError(res.error_code or "AI_2002", res.user_message or "商品查询失败", http_status=400)
    return res.data


async def _revalidate_stale(ctx: ToolContext, lines: list[AiCartItem]) -> list[dict]:
    """stale 行重校验：更新 price_cached / verified_at；商品失效则移除该行。"""
    now = datetime.now()
    out = []
    db = _db()
    try:
        detail_cache: dict[int, dict] = {}
        for l in lines:
            stale = (now - l.verified_at).total_seconds() > STALE_SECONDS
            if stale:
                try:
                    if l.product_id not in detail_cache:
                        detail_cache[l.product_id] = await get_java_detail(ctx, l.product_id)
                    d = detail_cache[l.product_id]
                    sku = next((s for s in d["skus"] if s["sku_unique"] == l.sku_unique), None)
                    if sku is None or (sku.get("stock") or 0) < l.quantity:
                        l.status = "removed"  # 已失效：移除
                        db.commit()
                        continue
                    l.price_cached = Decimal(str(sku["price"]))
                    l.verified_at = now
                    db.commit()
                except AppError:
                    l.status = "removed"
                    db.commit()
                    continue
            out.append({
                "cart_item_id": l.id,
                "product_id": l.product_id,
                "sku_unique": l.sku_unique,
                "name": l.product_name,
                "spec": l.spec_text,
                "quantity": l.quantity,
                "price_cached": str(l.price_cached),
                "subtotal_display": str(l.price_cached * l.quantity),
                "verified": not stale,
            })
        return out
    finally:
        db.close()


# ---------- 工具实现 ----------

async def add_to_cart(ctx: ToolContext, product_id: int, sku_unique: str, quantity: int = 1) -> ToolResult:
    try:
        info = await _validated_line(ctx, product_id, sku_unique, quantity)
    except AppError as e:
        return ToolResult.from_error(e)

    db = _db()
    try:
        existing = (
            db.query(AiCartItem)
            .filter(AiCartItem.session_id == ctx.session_id,
                    AiCartItem.status == "active",
                    AiCartItem.product_id == product_id,
                    AiCartItem.sku_unique == sku_unique)
            .first()
        )
        if existing:
            new_qty = existing.quantity + quantity
            if new_qty > MAX_QTY:
                return ToolResult.fail("AI_0001", f"单个商品数量不能超过 {MAX_QTY}")
            existing.quantity = new_qty
            existing.price_cached = info["price_cached"]
            existing.verified_at = datetime.now()
            existing.updated_at = datetime.now()
        else:
            existing = AiCartItem(
                session_id=ctx.session_id, member_id=ctx.member_id, shop_id=ctx.shop_id or 0,
                product_id=product_id, sku_unique=sku_unique,
                product_name=info["product_name"], spec_text=info["spec_text"],
                quantity=quantity, price_cached=info["price_cached"],
                verified_at=datetime.now(),
                created_at=datetime.now(), updated_at=datetime.now(),
            )
            db.add(existing)
        db.commit()
        return ToolResult.success(
            {"cart_item_id": existing.id, "name": info["product_name"], "spec": info["spec_text"],
             "quantity": existing.quantity, "unit_price_display": str(info["price_cached"])},
            item=info["product_name"], quantity=existing.quantity,
        )
    finally:
        db.close()


async def update_cart_item(ctx: ToolContext, cart_item_id: int,
                           quantity: int | None = None, sku_unique: str | None = None) -> ToolResult:
    db = _db()
    try:
        line = db.get(AiCartItem, cart_item_id)
        if line is None or line.status != "active" or line.member_id != ctx.member_id \
                or line.session_id != ctx.session_id:
            return ToolResult.fail("AI_0004", "购物车项不存在")
        new_sku = sku_unique or line.sku_unique
        new_qty = quantity if quantity is not None else line.quantity
        try:
            info = await _validated_line(ctx, line.product_id, new_sku, new_qty)
        except AppError as e:
            return ToolResult.from_error(e)
        line.sku_unique = new_sku
        line.spec_text = info["spec_text"]
        line.quantity = new_qty
        line.price_cached = info["price_cached"]
        line.verified_at = datetime.now()
        line.updated_at = datetime.now()
        db.commit()
        return ToolResult.success(
            {"cart_item_id": line.id, "name": line.product_name, "spec": line.spec_text,
             "quantity": line.quantity, "unit_price_display": str(line.price_cached)},
            item=line.product_name, quantity=line.quantity,
        )
    finally:
        db.close()


async def remove_cart_item(ctx: ToolContext, cart_item_id: int) -> ToolResult:
    db = _db()
    try:
        line = db.get(AiCartItem, cart_item_id)
        if line is None or line.status != "active" or line.member_id != ctx.member_id \
                or line.session_id != ctx.session_id:
            return ToolResult.fail("AI_0004", "购物车项不存在")
        line.status = "removed"
        line.updated_at = datetime.now()
        db.commit()
        return ToolResult.success({"cart_item_id": line.id}, item=line.product_name, removed=True)
    finally:
        db.close()


async def get_cart(ctx: ToolContext) -> ToolResult:
    db = _db()
    try:
        lines = _active_lines(db, ctx.session_id)
    finally:
        db.close()
    items = await _revalidate_stale(ctx, lines)
    total_display = sum(float(i["subtotal_display"]) for i in items)
    return ToolResult.success(
        {"items": items, "count": len(items),
         "total_display": f"{total_display:.2f}",
         "note": "合计仅为展示参考，最终金额以订单预览/下单时系统计算为准"},
        count=len(items),
    )


def register(registry: ToolRegistry) -> None:
    qty_schema = {"type": "integer", "minimum": 1, "maximum": MAX_QTY}
    registry.register(ToolSpec(
        name="add_to_cart",
        description="把商品加入购物车。需要商品 id 与规格 sku_unique（来自 get_dish_detail 的 skus）。"
                    "用户明确表示要加购/来一份时调用。同商品同规格自动合并数量。",
        parameters={
            "type": "object",
            "properties": {
                "product_id": {"type": "integer", "description": "商品 id"},
                "sku_unique": {"type": "string", "description": "规格唯一码（单规格商品用其唯一 sku）"},
                "quantity": qty_schema,
            },
            "required": ["product_id", "sku_unique"],
        },
        handler=add_to_cart,
        allowed_intents={"ordering"},
    ))
    registry.register(ToolSpec(
        name="update_cart_item",
        description="修改购物车项：改数量或改规格。cart_item_id 来自 get_cart。",
        parameters={
            "type": "object",
            "properties": {
                "cart_item_id": {"type": "integer"},
                "quantity": qty_schema,
                "sku_unique": {"type": "string"},
            },
            "required": ["cart_item_id"],
        },
        handler=update_cart_item,
        allowed_intents={"ordering"},
    ))
    registry.register(ToolSpec(
        name="remove_cart_item",
        description="从购物车移除一个商品项。cart_item_id 来自 get_cart。",
        parameters={
            "type": "object",
            "properties": {"cart_item_id": {"type": "integer"}},
            "required": ["cart_item_id"],
        },
        handler=remove_cart_item,
        allowed_intents={"ordering"},
    ))
    registry.register(ToolSpec(
        name="get_cart",
        description="查看当前购物车内容与数量。用户问'我点了什么/购物车'时使用。",
        parameters={"type": "object", "properties": {}},
        handler=get_cart,
        allowed_intents={"ordering"},
    ))
