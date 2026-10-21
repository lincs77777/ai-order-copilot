"""SSE 对话接口（设计文档 §5.1 POST /api/v1/chat）。

事件流（均带递增 seq）：meta / intent / delta / tool / done / error
反代注意：X-Accel-Buffering: no（防假流式）
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as OrmSession

from app.agent import context as ctx_mod
from app.agent.orchestrator import request_cancel, run_turn
from app.agent.router import classify
from app.api.deps import UserContext, get_current_user, get_rate_limiter
from app.core.errors import AppError, ErrorCode
from app.core.ratelimit import RateLimiter
from app.models.db import get_db
from app.tools.registry import get_registry

router = APIRouter()

MAX_MESSAGE_LEN = 500


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LEN)
    channel: str = "h5"


class CancelRequest(BaseModel):
    session_id: str


def _sse(event: str, data: dict, seq: int) -> str:
    return f"event: {event}\ndata: {json.dumps({**data, 'seq': seq}, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    user: UserContext = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    # 限流（用户 + IP）
    rl: RateLimiter = get_rate_limiter()
    rl.check_user(user.member_id)
    rl.check_ip(request.client.host if request.client else "unknown")

    # 会话：取或建（校验归属）
    try:
        session = ctx_mod.get_or_create_session(db, body.session_id, user.member_id, user.shop_id, body.channel)
    except ValueError:
        raise AppError(*ErrorCode.FORBIDDEN, http_status=403, detail="session 不属于当前用户")

    # 单会话并发 1 路
    if not rl.try_acquire_session_lock(session.id, ttl_seconds=120):
        raise AppError("AI_0008", "当前会话正在回复中，请稍候", http_status=429)

    intent = await classify(body.message)
    # 落库用户消息
    ctx_mod.save_message(db, session.id, "user", body.message, intent=intent)

    async def gen():
        seq = 0
        done_reason = "unknown"
        error_code: str | None = None
        degraded = False
        try:
            yield _sse("meta", {"session_id": session.id, "reply_id": session.id}, seq)
            seq += 1
            yield _sse("intent", {"type": intent}, seq)
            seq += 1

            registry = get_registry()
            async for kind, payload in run_turn(
                db, registry, session.id, user.member_id, user.shop_id,
                user_token=request.state.user_token,
                user_message=body.message, intent=intent,
            ):
                if kind == "delta":
                    yield _sse("delta", {"text": payload}, seq)
                elif kind == "tool":
                    yield _sse("tool", payload, seq)
                elif kind == "order_preview":
                    yield _sse("order_preview", payload, seq)
                elif kind == "error":
                    error_code = payload.get("code")
                    degraded = degraded or bool(payload.get("degraded"))
                    yield _sse("error", payload, seq)
                elif kind == "done":
                    done_reason = payload
                    yield _sse("done", {"finish_reason": payload}, seq)
                seq += 1
        except AppError as e:
            error_code, degraded, done_reason = e.code, e.degraded, "fallback"
            yield _sse("error", {"code": e.code, "message": e.user_message,
                                 "degraded": e.degraded}, seq)
            yield _sse("done", {"finish_reason": "fallback"}, seq)
        except Exception:  # 兜底，保证 SSE 以 done 收尾
            error_code, degraded, done_reason = "AI_0500", True, "fallback"
            yield _sse("error", {"code": "AI_0500", "message": "服务内部错误",
                                 "degraded": True}, seq)
            yield _sse("done", {"finish_reason": "fallback"}, seq)
            raise
        finally:
            _persist_trace(db, done_reason, error_code, degraded, intent, body.channel)
            get_rate_limiter().release_session_lock(session.id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _persist_trace(db, done_reason: str, error_code: str | None,
                   degraded: bool, intent: str, channel: str) -> None:
    """trace 落库 + 指标上报（设计 §9）。失败不影响主流程。"""
    from app.core.config import get_settings
    from app.observability import metrics as mt
    from app.observability import trace as trace_mod

    t = trace_mod.current()
    if t is None:
        return
    try:
        t.model = get_settings().LLM_MODEL_MAIN
        t.finish(done_reason, degraded=degraded, error_code=error_code)
        t.persist(db)

        mt.chat_requests_total.labels(intent=intent, channel=channel).inc()
        mt.chat_duration_seconds.observe(t.latency_ms / 1000)
        if t.prompt_tokens:
            mt.tokens_total.labels(kind="prompt").inc(t.prompt_tokens)
        if t.completion_tokens:
            mt.tokens_total.labels(kind="completion").inc(t.completion_tokens)
        if done_reason == "fallback":
            mt.fallback_total.labels(reason=error_code or "unknown").inc()
    except Exception:
        pass
    finally:
        trace_mod.clear()


@router.post("/chat/cancel")
def cancel(body: CancelRequest, user: UserContext = Depends(get_current_user),
           db: OrmSession = Depends(get_db)):
    from app.models.session import AiSession
    session = db.get(AiSession, body.session_id)
    if session is None or session.member_id != user.member_id:
        raise AppError(*ErrorCode.NOT_FOUND, http_status=404)
    request_cancel(body.session_id)
    return {"cancelled": True}
