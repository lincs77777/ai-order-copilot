"""工具注册表单测：参数校验 / 意图白名单 / internal_only（不依赖网络）。"""
from __future__ import annotations

import pytest

from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry, ToolSpec


@pytest.mark.asyncio
async def test_missing_required_arg():
    reg = ToolRegistry()

    async def handler(ctx, product_id: int):
        return ToolResult.success({})

    reg.register(ToolSpec(
        name="t", description="t",
        parameters={"type": "object", "properties": {
            "product_id": {"type": "integer", "minimum": 1}},
            "required": ["product_id"]},
        handler=handler,
    ))
    res = await reg.execute("t", {}, user_token="x", member_id=1, shop_id=1)
    assert not res.ok and res.error_code == "AI_0001"

    res = await reg.execute("t", {"product_id": 0}, user_token="x", member_id=1, shop_id=1)
    assert not res.ok and "不能小于" in res.user_message

    res = await reg.execute("t", {"product_id": "abc"}, user_token="x", member_id=1, shop_id=1)
    assert not res.ok and "应为整数" in res.user_message


@pytest.mark.asyncio
async def test_intent_whitelist():
    reg = ToolRegistry()

    async def handler(ctx):
        return ToolResult.success({})

    reg.register(ToolSpec(name="write_thing", description="",
                          parameters={"type": "object", "properties": {}},
                          handler=handler, allowed_intents={"ordering"}))
    # faq 意图不可用
    res = await reg.execute("write_thing", {}, user_token="x", member_id=1, shop_id=1, intent="faq")
    assert not res.ok and res.error_code == "AI_0003"
    # LLM 下发列表也不含
    assert reg.specs_for_llm("faq") == []
    assert len(reg.specs_for_llm("ordering")) == 1


@pytest.mark.asyncio
async def test_internal_only_never_callable_from_dialog():
    reg = ToolRegistry()

    async def handler(ctx):
        return ToolResult.success({})

    reg.register(ToolSpec(name="order_submit", description="",
                          parameters={"type": "object", "properties": {}},
                          handler=handler, internal_only=True))
    res = await reg.execute("order_submit", {}, user_token="x", member_id=1, shop_id=1, intent="ordering")
    assert not res.ok and res.error_code == "AI_0003"
    assert reg.specs_for_llm() == []


@pytest.mark.asyncio
async def test_unknown_tool():
    reg = ToolRegistry()
    res = await reg.execute("nope", {}, user_token="x", member_id=1, shop_id=1)
    assert not res.ok and res.error_code == "AI_0004"
