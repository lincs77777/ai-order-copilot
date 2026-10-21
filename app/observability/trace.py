"""全链路 trace（设计文档 §9）：每轮对话一条完整记录，可按 session/member/时间回放。

收集内容：用户问题、意图、RAG 检索命中、每轮模型输出计量、每次工具调用（名称/参数摘要/
状态/耗时/结果摘要）、token 汇总、耗时、结束原因、是否降级。
"""
from __future__ import annotations

import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.models.audit import AiTrace


@dataclass
class TurnTrace:
    session_id: str
    member_id: int
    shop_id: int | None
    intent: str
    user_message: str
    request_id: str = ""
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_calls: list[dict] = field(default_factory=list)
    retrieval: list[dict] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    latency_ms: int = 0
    finish_reason: str = ""
    degraded: bool = False
    error_code: str | None = None
    _t0: float = field(default_factory=time.monotonic)

    # ---- 记录点 ----
    def record_tool(self, name: str, args: dict | None, status: str,
                    summary: dict | None = None, error: str | None = None,
                    latency_ms: int | None = None) -> None:
        self.tool_calls.append({
            "name": name, "args": _digest(args), "status": status,
            "summary": summary or {}, "error": error, "latency_ms": latency_ms,
        })

    def record_retrieval(self, hits: list) -> None:
        self.retrieval = [{"doc_id": h.doc_id, "score": round(h.score, 3),
                           "source": h.metadata.get("source")} for h in hits[:8]]

    def record_round(self, round_no: int, finish: str | None,
                     prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.rounds.append({"round": round_no, "finish": finish,
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens})
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    def finish(self, reason: str, degraded: bool = False, error_code: str | None = None) -> "TurnTrace":
        self.finish_reason = reason
        self.degraded = degraded
        self.error_code = error_code
        self.latency_ms = int((time.monotonic() - self._t0) * 1000)
        return self

    def persist(self, db: OrmSession) -> None:
        from datetime import datetime
        db.add(AiTrace(
            trace_id=self.trace_id, session_id=self.session_id, member_id=self.member_id,
            shop_id=self.shop_id or 0, request_id=self.request_id, intent=self.intent,
            user_message=self.user_message[:500],
            tool_calls=json.dumps(self.tool_calls, ensure_ascii=False),
            retrieval=json.dumps(self.retrieval, ensure_ascii=False),
            rounds=json.dumps(self.rounds, ensure_ascii=False),
            prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens,
            model=self.model, latency_ms=self.latency_ms,
            finish_reason=self.finish_reason, degraded=self.degraded,
            error_code=self.error_code,
            created_at=datetime.now(),
        ))
        try:
            db.commit()
        except Exception:
            db.rollback()


def _digest(args: dict | None, limit: int = 300) -> dict:
    """参数摘要（避免长文本入库）。"""
    if not args:
        return {}
    out = {}
    for k, v in args.items():
        s = str(v)
        out[k] = s[:80]
    body = json.dumps(out, ensure_ascii=False)
    if len(body) > limit:
        return {"_truncated": body[:limit]}
    return out


_current: ContextVar[TurnTrace | None] = ContextVar("turn_trace", default=None)


def start(session_id: str, member_id: int, shop_id: int | None,
          intent: str, user_message: str, request_id: str = "") -> TurnTrace:
    t = TurnTrace(session_id=session_id, member_id=member_id, shop_id=shop_id,
                  intent=intent, user_message=user_message, request_id=request_id)
    _current.set(t)
    return t


def current() -> TurnTrace | None:
    return _current.get()


def clear() -> None:
    _current.set(None)
