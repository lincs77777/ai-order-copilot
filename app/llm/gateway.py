"""LLM 网关客户端（设计文档 §3 llm/gateway.py）。

DeepSeek 直连（OpenAI 兼容端点），adapter 留口后续切 One-API/LiteLLM。
- 流式输出：文本增量 / tool_calls 聚合 / usage 计量
- 重试：首个 chunk 到达前的连接类失败重试 2 次（流中途失败不重试，避免重复输出）
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, RateLimitError

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode


@dataclass
class ToolCallOut:
    id: str
    name: str
    arguments: str  # JSON 字符串（原样透传给上层解析）


@dataclass
class StreamResult:
    """一次流式调用的归集结果。"""
    text_parts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallOut] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


@lru_cache
def _client() -> AsyncOpenAI:
    s = get_settings()
    return AsyncOpenAI(
        api_key=s.LLM_API_KEY,
        base_url=s.LLM_BASE_URL,
        timeout=s.LLM_TIMEOUT_SECONDS,
        max_retries=0,  # 重试自己做（首 chunk 前）
    )


async def stream_chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float = 0.0,
) -> AsyncGenerator[tuple[str, Any], None]:
    """流式对话。

    产出事件元组：
      ("delta", str)                     文本增量
      ("tool_calls", list[ToolCallOut])  完整聚合后的工具调用（流结束时一次给出）
      ("usage", (prompt, completion))    token 计量（若上游返回）
      ("finish", str)                    结束原因 stop/tool_calls/...
    失败抛 AppError（AI_3xxx）。
    """
    s = get_settings()
    model = model or s.LLM_MODEL_MAIN
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        kwargs["tools"] = tools

    last_exc: Exception | None = None
    for attempt in range(3):  # 1 次正常 + 2 次重试（仅覆盖首 chunk 前失败）
        try:
            stream = await _client().chat.completions.create(**kwargs)
            first = await stream.__anext__()  # 触发连接，首 chunk 前的失败可重试
            break
        except (APIConnectionError, APITimeoutError, RateLimitError, asyncio.TimeoutError) as e:
            last_exc = e
            if attempt == 2:
                code, msg = (ErrorCode.LLM_TIMEOUT if isinstance(e, (APITimeoutError, asyncio.TimeoutError))
                             else ErrorCode.LLM_QUOTA_EXCEEDED if isinstance(e, RateLimitError)
                             else ErrorCode.LLM_TIMEOUT)
                raise AppError(code, msg, http_status=502, detail=str(e), degraded=True)
            await asyncio.sleep(0.5 * (attempt + 1))
        except Exception as e:  # 其他 SDK 异常不重试
            raise AppError("AI_3003", "AI 服务异常", http_status=502, detail=str(e), degraded=True)
    else:  # pragma: no cover
        raise AppError(ErrorCode.LLM_TIMEOUT[0], ErrorCode.LLM_TIMEOUT[1],
                       http_status=502, detail=str(last_exc), degraded=True)

    # 聚合状态
    text_acc: list[str] = []
    tool_acc: dict[int, dict[str, str]] = {}  # index -> {id, name, arguments}
    finish: str | None = None
    usage: tuple[int, int] | None = None

    def _flush_tool_calls() -> list[ToolCallOut]:
        out = []
        for idx in sorted(tool_acc):
            t = tool_acc[idx]
            out.append(ToolCallOut(id=t.get("id", ""), name=t.get("name", ""), arguments=t.get("arguments", "")))
        return out

    chunk = first
    emitted_tool_call_event = False
    while True:
        if getattr(chunk, "usage", None):
            usage = (chunk.usage.prompt_tokens or 0, chunk.usage.completion_tokens or 0)
        if chunk.choices:
            choice = chunk.choices[0]
            delta = choice.delta
            if delta is not None:
                if delta.content:
                    text_acc.append(delta.content)
                    yield ("delta", delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        slot = tool_acc.setdefault(tc.index or 0, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
            if choice.finish_reason:
                finish = choice.finish_reason
                if finish == "tool_calls" and not emitted_tool_call_event:
                    calls = _flush_tool_calls()
                    if calls:
                        emitted_tool_call_event = True
                        yield ("tool_calls", calls)
        try:
            chunk = await stream.__anext__()
        except StopAsyncIteration:
            break

    if finish is None:
        finish = "tool_calls" if tool_acc else "stop"
    if usage:
        yield ("usage", usage)
    if not emitted_tool_call_event and tool_acc:
        yield ("tool_calls", _flush_tool_calls())
    yield ("finish", finish)
