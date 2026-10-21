"""菜单工具：查菜单 / 查商品详情（设计文档 §5.2）。

- list_dishes：GET /app-api/product/products（分类嵌套结构拍平），60s 缓存
- get_dish_detail：GET /app-api/product/detail/{id}，门店归属校验（借分类列表缓存核对）
"""
from __future__ import annotations

import time
from typing import Any

from app.core.java_client import get_java_client, resolve_upstream_token
from app.core.errors import AppError, ErrorCode
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec

_LIST_TTL = 60.0

# {shop_id: (expires_at, {product_id: {store_name, price, is_show}})}
_list_cache: dict[int, tuple[float, dict[int, dict[str, Any]]]] = {}


def invalidate_menu_cache(shop_id: int | None = None) -> None:
    if shop_id is None:
        _list_cache.clear()
    else:
        _list_cache.pop(shop_id, None)


async def _fetch_product_index(ctx: ToolContext) -> dict[int, dict[str, Any]]:
    """拉取当前门店商品索引（带 60s 缓存）。"""
    now = time.monotonic()
    cached = _list_cache.get(ctx.shop_id)
    if cached and cached[0] > now:
        return cached[1]

    data = await get_java_client().get(
        "/app-api/product/products",
        token=resolve_upstream_token(ctx.user_token),
        params={"shopId": ctx.shop_id},
    )
    index: dict[int, dict[str, Any]] = {}
    for cate in data or []:
        for g in cate.get("goodsList") or []:
            pid = int(g["id"])
            index[pid] = {
                "id": pid,
                "store_name": g.get("storeName"),
                "price": str(g.get("price")),
                "stock": g.get("stock"),
                "is_show": g.get("isShow"),
                "sales": g.get("sales"),
                "image": g.get("image"),
                "cate_name": cate.get("name"),
                "store_info": g.get("storeInfo"),
            }
    _list_cache[ctx.shop_id] = (now + _LIST_TTL, index)
    return index


async def list_dishes(ctx: ToolContext, keyword: str | None = None) -> ToolResult:
    """查当前门店菜单；keyword 对店名/描述做不区分大小写包含匹配。"""
    try:
        index = await _fetch_product_index(ctx)
    except AppError as e:
        return ToolResult.from_error(e)

    items = list(index.values())
    if keyword:
        kw = keyword.lower()
        items = [it for it in items
                 if kw in (it.get("store_name") or "").lower()
                 or kw in (it.get("store_info") or "").lower()
                 or kw in (it.get("cate_name") or "").lower()]
    on_sale = [it for it in items if it.get("is_show") == 1]
    return ToolResult.success(
        {"items": on_sale, "total": len(on_sale)},
        count=len(on_sale),
    )


async def get_dish_detail(ctx: ToolContext, product_id: int) -> ToolResult:
    """查单品详情（规格/SKU/价格/库存），并校验归属当前门店。"""
    try:
        index = await _fetch_product_index(ctx)
        if product_id not in index:
            return ToolResult.fail(
                "AI_2003", "商品不存在或不属于当前门店", data={"product_id": product_id})
        detail = await get_java_client().get(
            f"/app-api/product/detail/{product_id}",
            token=resolve_upstream_token(ctx.user_token),
        )
    except AppError as e:
        return ToolResult.from_error(e)

    # SKU 规范化：productValue: {sku_unique: attr}
    skus = []
    for unique, attr in (detail.get("productValue") or {}).items():
        skus.append({
            "sku_unique": unique,
            "spec": attr.get("spec") or unique,
            "price": str(attr.get("price")),
            "stock": attr.get("stock"),
        })
    return ToolResult.success(
        {
            "product_id": detail.get("id"),
            "store_name": detail.get("storeName"),
            "store_info": detail.get("storeInfo"),
            "price": str(detail.get("price")),
            "stock": detail.get("stock"),
            "is_show": detail.get("isShow"),
            "image": detail.get("image"),
            "skus": skus,
        },
        item=detail.get("storeName"),
    )


def register(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        name="list_dishes",
        description="查询当前门店的菜单（在售商品列表），可按关键词过滤。用户问'有什么/菜单/推荐'时使用。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "关键词（菜品名/分类名），可省略"},
            },
        },
        handler=list_dishes,
        allowed_intents={"ordering"},
    ))
    registry.register(ToolSpec(
        name="get_dish_detail",
        description="查询单个菜品的详情：规格/SKU、价格、库存。用户指定某个菜品要了解规格价格时使用。",
        parameters={
            "type": "object",
            "properties": {
                "product_id": {"type": "integer", "description": "商品ID（来自 list_dishes 返回的 id）"},
            },
            "required": ["product_id"],
        },
        handler=get_dish_detail,
        allowed_intents={"ordering"},
    ))
