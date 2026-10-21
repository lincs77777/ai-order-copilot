"""工具注册表：JSON Schema 定义 + 参数简校验 + 按意图白名单（设计文档 §3/§8.1）。

注：参数校验为 required + 顶层类型检查（覆盖 LLM 生成参数的主要错误形态）；
S9 守卫层再叠加语义校验（数量区间、门店归属断言）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.tools.base import ToolContext, ToolHandler, ToolResult

_INTENT_ALL = {"ordering", "faq", "rejected"}


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]           # JSON Schema（type: object）
    handler: ToolHandler
    allowed_intents: set[str] = field(default_factory=lambda: set(_INTENT_ALL))
    # 写工具标记：order_submit 这类只能由确认接口触发，不可被对话文本直接调用
    internal_only: bool = False


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def specs_for_llm(self, intent: str | None = None) -> list[dict[str, Any]]:
        """给 LLM 的 tools 列表：按意图过滤白名单，internal_only 永不下发。"""
        out = []
        for t in self._tools.values():
            if t.internal_only:
                continue
            if intent is not None and intent not in t.allowed_intents:
                continue
            out.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            })
        return out

    def check_allowed(self, name: str, intent: str | None) -> bool:
        t = self._tools.get(name)
        if t is None or t.internal_only:
            return False
        if intent is not None and intent not in t.allowed_intents:
            return False
        return True

    async def execute(self, name: str, args: dict[str, Any], **ctx_kwargs) -> ToolResult:
        """白名单校验 + 参数简校验 + 执行。任何异常转失败 ToolResult。"""
        t = self._tools.get(name)
        if t is None:
            return ToolResult.fail("AI_0004", f"工具 {name} 不存在")
        if t.internal_only:
            return ToolResult.fail("AI_0003", f"工具 {name} 不可由对话直接调用")

        intent = ctx_kwargs.get("intent")
        if intent is not None and intent not in t.allowed_intents:
            return ToolResult.fail("AI_0003", f"当前意图下不可使用工具 {name}")

        args = args or {}
        props = t.parameters.get("properties", {})
        required = t.parameters.get("required", [])
        for r in required:
            if r not in args or args[r] in (None, ""):
                return ToolResult.fail("AI_0001", f"参数 {r} 缺失")
        for k, v in args.items():
            spec = props.get(k)
            if not spec:
                continue
            expected = spec.get("type")
            if expected == "integer" and not isinstance(v, int):
                return ToolResult.fail("AI_0001", f"参数 {k} 应为整数")
            if expected == "string" and not isinstance(v, str):
                return ToolResult.fail("AI_0001", f"参数 {k} 应为字符串")
            if expected == "number" and not isinstance(v, (int, float)):
                return ToolResult.fail("AI_0001", f"参数 {k} 应为数字")
            minimum, maximum = spec.get("minimum"), spec.get("maximum")
            if isinstance(v, (int, float)):
                if minimum is not None and v < minimum:
                    return ToolResult.fail("AI_0001", f"参数 {k} 不能小于 {minimum}")
                if maximum is not None and v > maximum:
                    return ToolResult.fail("AI_0001", f"参数 {k} 不能大于 {maximum}")

        try:
            ctx = ToolContext(
                user_token=ctx_kwargs.get("user_token", ""),
                member_id=ctx_kwargs.get("member_id", 0),
                shop_id=ctx_kwargs.get("shop_id"),
                session_id=ctx_kwargs.get("session_id", ""),
            )
            return await t.handler(ctx, **args)
        except Exception as e:  # 兜底：工具绝不抛异常出注册表
            return ToolResult.fail("AI_0500", f"工具 {name} 执行异常", data=str(e))


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        from app.tools import cart_tools, knowledge_tools, menu_tools, order_tools, shop_tools
        _registry = ToolRegistry()
        menu_tools.register(_registry)
        shop_tools.register(_registry)
        cart_tools.register(_registry)
        order_tools.register(_registry)
        knowledge_tools.register(_registry)
    return _registry
