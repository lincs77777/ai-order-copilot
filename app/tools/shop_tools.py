"""门店工具：查门店信息（复用既有 GET /app-api/store/getShop，5min 缓存）。"""
from __future__ import annotations

import time
from typing import Any

from app.core.errors import AppError
from app.core.java_client import get_java_client, resolve_upstream_token
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec

_SHOP_TTL = 300.0
_shop_cache: dict[int, tuple[float, dict[str, Any]]] = {}


def invalidate_shop_cache(shop_id: int | None = None) -> None:
    if shop_id is None:
        _shop_cache.clear()
    else:
        _shop_cache.pop(shop_id, None)


async def get_shop_info(ctx: ToolContext) -> ToolResult:
    """查当前门店信息（名称/地址/营业时间/配送费等）。"""
    now = time.monotonic()
    cached = _shop_cache.get(ctx.shop_id)
    if cached and cached[0] > now:
        return ToolResult.success(cached[1], shop=cached[1].get("name"))
    try:
        data = await get_java_client().get(
            "/app-api/store/getShop",
            token=resolve_upstream_token(ctx.user_token),
            params={"shop_id": ctx.shop_id},
        )
    except AppError as e:
        return ToolResult.from_error(e)

    info = {
        "shop_id": ctx.shop_id,
        "name": (data or {}).get("name"),
        "address": (data or {}).get("address"),
        "phone": (data or {}).get("phone"),
        "latitude": (data or {}).get("latitude"),
        "longitude": (data or {}).get("longitude"),
        "delivery_price": str((data or {}).get("deliveryPrice", "")),
        # 原始字段透出，避免遗漏营业时间等未建模字段
        "raw": data or {},
    }
    _shop_cache[ctx.shop_id] = (now + _SHOP_TTL, info)
    return ToolResult.success(info, shop=info.get("name"))


def register(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        name="get_shop_info",
        description="查询当前门店的信息：名称、地址、电话、配送费等。用户问门店/营业相关问题时使用。",
        parameters={"type": "object", "properties": {}},
        handler=get_shop_info,
        allowed_intents={"ordering"},
    ))
