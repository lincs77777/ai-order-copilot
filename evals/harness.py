"""评测执行器：在进程内驱动 orchestrator 跑多轮对话，收集事件与终态。"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent import context as ctx_mod
from app.models.db import SessionLocal
from app.agent.orchestrator import run_turn
from app.core.config import get_settings
from app.models.cart import AiCartItem
from app.tools.base import ToolContext
from app.tools.registry import get_registry
from app.tools.order_tools import order_submit


@dataclass
class SessionResult:
    session_id: str
    texts: list[str] = field(default_factory=list)
    tool_events: list[dict] = field(default_factory=list)
    error_events: list[dict] = field(default_factory=list)
    previews: list[dict] = field(default_factory=list)
    finish_reasons: list[str] = field(default_factory=list)
    rounds: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def full_text(self) -> str:
        return "\n".join(self.texts)

    def tool_called(self, name: str) -> bool:
        return any(t["name"] == name for t in self.tool_events)


async def run_session(turns: list[str], member_id: int = 9527, shop_id: int = 2,
                      setup_cart: list[dict] | None = None) -> SessionResult:
    """新建会话跑多轮；返回事件与终态。"""
    import uuid
    s = get_settings()
    db = SessionLocal()
    try:
        session_id = str(uuid.uuid4())
        from app.models.session import AiSession
        db.add(AiSession(id=session_id, member_id=member_id, shop_id=shop_id,
                         created_at=datetime.now(), updated_at=datetime.now()))
        db.commit()

        if setup_cart:
            now = datetime.now()
            for it in setup_cart:
                db.add(AiCartItem(session_id=session_id, member_id=member_id, shop_id=shop_id,
                                  product_id=it["product_id"], sku_unique=it["sku_unique"],
                                  product_name="测试商品", quantity=it["quantity"],
                                  price_cached=0.01, verified_at=now,
                                  created_at=now, updated_at=now))
                db.commit()

        result = SessionResult(session_id=session_id)
        registry = get_registry()

        from app.agent.router import classify
        for user_message in turns:
            intent = await classify(user_message)
            ctx_mod.save_message(db, session_id, "user", user_message, intent=intent)
            turn_buf_local = [[]]
            async for kind, payload in run_turn(
                db, registry, session_id, member_id, shop_id,
                user_token="eval", user_message=user_message, intent=intent,
            ):
                turn_buf = turn_buf_local[0]
                if kind == "delta":
                    turn_buf.append(payload)
                elif kind == "tool":
                    result.tool_events.append(payload)
                    result.rounds += 1
                elif kind == "error":
                    result.error_events.append(payload)
                elif kind == "order_preview":
                    result.previews.append(payload)
                elif kind == "done":
                    if turn_buf:
                        result.texts.append("".join(turn_buf))
                        turn_buf_local[0] = []
                    result.finish_reasons.append(payload)
        # 汇总 token（读库）
        from app.models.session import AiMessage
        rows = db.query(AiMessage).filter(AiMessage.session_id == session_id).all()
        result.prompt_tokens = sum(r.prompt_tokens or 0 for r in rows)
        result.completion_tokens = sum(r.completion_tokens or 0 for r in rows)
        # 还原完整文本（assistant 行）
        assistant_texts = [r.content for r in rows if r.role == "assistant" and r.content]
        if assistant_texts:
            result.texts = assistant_texts
        return result
    finally:
        db.close()


def get_cart_lines(session_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        lines = (db.query(AiCartItem)
                 .filter(AiCartItem.session_id == session_id, AiCartItem.status == "active").all())
        return [{"product_id": l.product_id, "sku_unique": l.sku_unique, "quantity": l.quantity}
                for l in lines]
    finally:
        db.close()


async def try_confirm(session_result: SessionResult, member_id: int = 9527) -> bool:
    """模拟前端确认：取最后一个 preview 提交。"""
    if not session_result.previews:
        return False
    preview_id = session_result.previews[-1]["preview_id"]
    ctx = ToolContext(user_token="eval", member_id=member_id, shop_id=2,
                      session_id=session_result.session_id)
    res = await order_submit(ctx, preview_id=preview_id)
    return res.ok
