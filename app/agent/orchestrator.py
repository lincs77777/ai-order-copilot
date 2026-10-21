"""Function Calling 主循环（设计文档 §4.1 / §3 agent/orchestrator.py）。

- 最多 FC_MAX_ROUNDS 轮
- 工具失败结构化回喂；同一错误连续 2 次终止（降级）
- 支持取消（每轮之间检查）
- 产出事件流：("delta", text) / ("tool", summary) / ("error", code, msg, degraded) / ("done", reason)
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import AsyncGenerator

from sqlalchemy.orm import Session as OrmSession

from app.agent import context as ctx_mod
from app.core.errors import AppError
from app.llm import gateway
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry

# 取消登记：session_id -> asyncio.Event
_cancel_events: dict[str, asyncio.Event] = {}


def request_cancel(session_id: str) -> bool:
    ev = _cancel_events.setdefault(session_id, asyncio.Event())
    ev.set()
    return True


def _is_cancelled(session_id: str) -> bool:
    ev = _cancel_events.get(session_id)
    return bool(ev and ev.is_set())


def _clear_cancel(session_id: str) -> None:
    _cancel_events.pop(session_id, None)


# 简易意图规则（S11 换成规则+轻量模型分流）
_ORDERING_WORDS = ["菜单", "推荐", "有什么", "来一份", "来个", "加购", "下单", "买", "喝", "吃", "购物车",
                   "点餐", "点单", "点一份", "点个", "我要"]
_FAQ_WORDS = ["是什么", "有没有", "几点", "营业", "地址", "电话", "配送", "门店", "过敏", "成分", "辣"]


def guess_intent(message: str) -> str:
    if any(w in message for w in _FAQ_WORDS) and not any(w in message for w in _ORDERING_WORDS):
        return "faq"
    return "ordering"


@dataclass
class RoundState:
    same_error_count: int = 0
    last_error_key: str | None = None


async def run_turn(
    db: OrmSession,
    registry: ToolRegistry,
    session_id: str,
    member_id: int,
    shop_id: int | None,
    user_token: str,
    user_message: str,
    intent: str | None = None,
) -> AsyncGenerator[tuple, None]:
    """执行一轮对话（内部最多 FC_MAX_ROUNDS 次模型调用），产出事件流。"""
    from app.core.config import get_settings
    from app.guard import content_filter, hallucination_guard, prompt_guard
    from app.agent.router import INTENT_REJECTED, REJECT_REPLY
    s = get_settings()
    intent = intent or guess_intent(user_message)
    _clear_cancel(session_id)

    # 全链路 trace（§9）
    from app.core.logging import get_request_id
    from app.observability import metrics as mt
    from app.observability import trace as trace_mod
    turn_trace = trace_mod.start(session_id, member_id, shop_id, intent,
                                 user_message, get_request_id())

    # 守卫①：高置信注入 → 不进 LLM 直接拒绝（AI_1001）
    injected, _pat = prompt_guard.detect_injection(user_message)
    if injected:
        mt.injection_blocked_total.inc()
        yield ("error", {"code": "AI_1001", "message": "您的请求包含不被支持的内容，请重新描述您的需求", "degraded": False})
        turn_trace.finish("rejected", error_code="AI_1001")
        yield ("done", "rejected")
        return
    user_message = prompt_guard.sanitize(user_message)

    # 意图分流：闲聊 → 礼貌拒绝，不进 LLM（§7.3）
    if intent == INTENT_REJECTED:
        yield ("delta", REJECT_REPLY)
        yield ("done", "stop")
        return

    # 熔断检查：开路期跳过 LLM，直接规则推荐（§7.4）
    from app.degeneration.fallback import get_breaker, rule_menu_recommendation
    breaker = get_breaker()
    if breaker.is_open():
        rec = await rule_menu_recommendation(
            ToolContext(user_token=user_token, member_id=member_id, shop_id=shop_id))
        yield ("delta", rec)
        yield ("error", {"code": "AI_9001", "message": "AI 服务熔断中，已返回规则推荐", "degraded": True})
        yield ("done", "fallback")
        return

    t0 = time.monotonic()
    from app.tools import cart_tools
    cart_summary = cart_tools.cart_snapshot_text(db, session_id)

    # faq 意图：RAG 检索拼入系统提示词（§4.2）；ordering 用点餐提示词
    prompt_name = "system_ordering.md"
    prompt_vars = {"shop_id": shop_id, "cart_summary": cart_summary}
    if intent == "faq":
        from app.rag import retriever as rag_retriever
        try:
            hits = await rag_retriever.retrieve(user_message)
        except Exception:
            hits = []
        from app.observability import metrics as mt
        from app.observability import trace as trace_mod
        mt.retrieval_hits.observe(len(hits))
        _t = trace_mod.current()
        if _t:
            _t.record_retrieval(hits)
        context_text = "\n\n".join(f"[{h.doc_id}] {h.text}" for h in hits[:6]) or "（无检索结果）"
        prompt_name = "system_faq.md"
        prompt_vars = {"shop_id": shop_id, "context": context_text}

    messages: list[dict] = [
        {"role": "system", "content": ctx_mod.load_system_prompt(prompt_name, **prompt_vars)},
        *ctx_mod.load_history(db, session_id),
        {"role": "user", "content": user_message},
    ]

    tools = registry.specs_for_llm(intent)
    state = RoundState()
    prompt_tokens = completion_tokens = 0
    degraded_reason = None
    final_text_parts: list[str] = []

    try:
        for round_no in range(1, s.FC_MAX_ROUNDS + 1):
            if _is_cancelled(session_id):
                yield ("done", "cancelled")
                return

            round_tool_calls = False
            _llm_t0 = time.monotonic()
            try:
                async for kind, payload in gateway.stream_chat(messages, tools=tools):
                    if kind == "delta":
                        final_text_parts.append(payload)
                        yield ("delta", payload)
                    elif kind == "usage":
                        prompt_tokens += payload[0]
                        completion_tokens += payload[1]
                    elif kind == "tool_calls":
                        round_tool_calls = True
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {"id": c.id, "type": "function",
                                 "function": {"name": c.name, "arguments": c.arguments}}
                                for c in payload
                            ],
                        })
                        for c in payload:
                            yield ("tool", {"name": c.name, "status": "running",
                                            "args": _safe_json(c.arguments)})
                            _tool_t0 = time.monotonic()
                            result = await registry.execute(
                                c.name, _safe_json(c.arguments) or {},
                                user_token=user_token, member_id=member_id,
                                shop_id=shop_id, session_id=session_id, intent=intent,
                            )
                            _tool_ms = int((time.monotonic() - _tool_t0) * 1000)
                            mt.tool_calls_total.labels(tool=c.name,
                                                       status="ok" if result.ok else "error").inc()
                            turn_trace.record_tool(c.name, _safe_json(c.arguments),
                                                   "ok" if result.ok else "error",
                                                   result.summary, result.user_message, _tool_ms)
                            yield ("tool", {"name": c.name,
                                            "status": "ok" if result.ok else "error",
                                            "summary": result.summary,
                                            "message": result.user_message})
                            if c.name == "order_preview" and result.ok:
                                mt.order_preview_total.inc()
                                yield ("order_preview", result.data)

                            if result.ok:
                                state.same_error_count = 0
                                state.last_error_key = None
                                feed = json.dumps({"result": result.data}, ensure_ascii=False, default=str)
                            else:
                                err_key = f"{c.name}:{result.error_code}"
                                state.same_error_count = (state.same_error_count + 1
                                                          if state.last_error_key == err_key else 1)
                                state.last_error_key = err_key
                                feed = json.dumps({"error": result.error_code,
                                                   "message": result.user_message},
                                                  ensure_ascii=False)
                                if state.same_error_count >= 2:
                                    degraded_reason = "工具连续执行失败，已停止本轮处理"

                            ctx_mod.save_message(db, session_id, "tool", feed,
                                                 intent=intent, tool_name=c.name,
                                                 tool_args=_safe_json(c.arguments))
                            messages.append({"role": "tool", "tool_call_id": c.id, "content": feed})

                        if degraded_reason:
                            yield ("error", {"code": "AI_2002", "message": degraded_reason, "degraded": True})
                            yield ("done", "fallback")
                            return
                # stream 结束：若本轮没有工具调用（finish=stop），文本已输出，结束循环
                _llm_ms = int((time.monotonic() - _llm_t0) * 1000)
                mt.llm_calls_total.labels(model=s.LLM_MODEL_MAIN).inc()
                mt.llm_duration_seconds.observe(_llm_ms / 1000)
                turn_trace.record_round(round_no, "tool_calls" if round_tool_calls else "stop",
                                        prompt_tokens, completion_tokens)
                if not round_tool_calls:
                    breaker.record_success()
                    break
            except AppError as e:
                if e.degraded:
                    breaker.record_failure()
                    yield ("error", {"code": e.code, "message": e.user_message, "degraded": True})
                    yield ("done", "fallback")
                    return
                raise
        else:
            yield ("error", {"code": "AI_9001", "message": "处理轮次已达上限，请换个说法重试", "degraded": True})
            yield ("done", "fallback")
            return

        # 守卫④：输出合规过滤（AI_1002）
        final_text = "".join(final_text_parts)
        violated, _kind = content_filter.filter_output(final_text)
        if violated:
            yield ("error", {"code": "AI_1002", "message": content_filter.SAFE_REPLY, "degraded": False})
            yield ("done", "filtered")
            return
        # 守卫③：过敏原"安全断言" → 追加保守话术（红线保障）
        claim, _m = hallucination_guard.check_allergen_safety_claim(final_text)
        if claim:
            notice = hallucination_guard.CONSERVATIVE_NOTICE
            final_text_parts.append(notice)
            yield ("delta", notice)

        yield ("done", "stop")
    finally:
        latency = int((time.monotonic() - t0) * 1000)
        ctx_mod.save_message(db, session_id, "assistant", "".join(final_text_parts),
                             intent=intent, prompt_tokens=prompt_tokens,
                             completion_tokens=completion_tokens, model=s.LLM_MODEL_MAIN,
                             latency_ms=latency)


def _safe_json(s: str) -> dict:
    try:
        return json.loads(s) if s else {}
    except json.JSONDecodeError:
        return {}
