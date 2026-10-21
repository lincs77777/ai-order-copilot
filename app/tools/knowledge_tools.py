"""知识工具：过敏原信息（检索知识库语料，非 Java 接口——yshop 无过敏原字段）。

语料未覆盖 → 返回 unknown，强制保守话术（设计文档 §5.2 / §7.1 第 0 条）。
"""
from __future__ import annotations

from app.rag import retriever
from app.tools.base import ToolContext, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec


async def get_allergen_info(ctx: ToolContext, product_name: str) -> ToolResult:
    hits = await retriever.retrieve(f"{product_name} 过敏原 成分 配料", filter_off_shelf=False)
    allergen_hits = [h for h in hits
                     if h.metadata.get("source") == "allergen"
                     and product_name in h.text]
    if not allergen_hits:
        return ToolResult.success({
            "status": "unknown",
            "reply_hint": "该商品成分信息待确认，建议咨询店员。绝不能断言不含过敏原或安全。",
        }, product=product_name)
    return ToolResult.success({
        "status": "known",
        "info": "\n".join(h.text for h in allergen_hits[:2]),
        "reply_hint": "按语料作答，并注明'以门店实际配料为准'。",
    }, product=product_name)


def register(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        name="get_allergen_info",
        description="查询商品的过敏原与成分信息（依据门店知识库语料）。语料未覆盖时返回 unknown，"
                    "此时必须回复'该商品成分信息待确认，建议咨询店员'，绝不断言安全。",
        parameters={
            "type": "object",
            "properties": {
                "product_name": {"type": "string", "description": "商品名称"},
            },
            "required": ["product_name"],
        },
        handler=get_allergen_info,
        allowed_intents={"ordering", "faq"},
    ))
