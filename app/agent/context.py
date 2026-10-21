"""会话上下文管理（设计文档 §7.2）。

- 历史保留最近 20 条消息（user/assistant），更早的暂不压缩（M3/S17 做摘要压缩）
- 每轮把系统提示词 + 历史 + 当前输入组装为 messages
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session as OrmSession

from app.models.session import AiMessage, AiSession

PROMPTS_DIR = Path(__file__).parent / "prompts"
MAX_HISTORY_MESSAGES = 20


def load_system_prompt(name: str, **kwargs) -> str:
    """加载版本化提示词模板并填充变量。"""
    template = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    defaults = {"shop_id": "", "today": date.today().isoformat()}
    defaults.update(kwargs)
    return template.format(**defaults)


def load_history(db: OrmSession, session_id: str, limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    """最近 N 条 user/assistant 消息（按时间正序）。"""
    rows = (
        db.query(AiMessage)
        .filter(AiMessage.session_id == session_id,
                AiMessage.role.in_(["user", "assistant"]))
        .order_by(AiMessage.id.desc())
        .limit(limit)
        .all()
    )
    return [{"role": r.role, "content": r.content or ""} for r in reversed(rows)]


def save_message(db: OrmSession, session_id: str, role: str, content: str | None = None,
                 intent: str | None = None, tool_name: str | None = None,
                 tool_args: dict | None = None, prompt_tokens: int = 0,
                 completion_tokens: int = 0, model: str | None = None,
                 latency_ms: int | None = None) -> AiMessage:
    msg = AiMessage(
        session_id=session_id, role=role, content=content, intent=intent,
        tool_name=tool_name, tool_args=tool_args,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        model=model, latency_ms=latency_ms, created_at=datetime.now(),
    )
    db.add(msg)
    db.commit()
    return msg


def get_or_create_session(db: OrmSession, session_id: str | None, member_id: int,
                          shop_id: int | None, channel: str = "h5") -> AiSession:
    """取会话（校验归属）；无则创建（绑定 member+shop）。"""
    if session_id:
        sess = db.get(AiSession, session_id)
        if sess is not None:
            if sess.member_id != member_id:
                raise ValueError("session_not_owned")
            return sess
    import uuid
    sess = AiSession(
        id=str(uuid.uuid4()), member_id=member_id,
        shop_id=shop_id or 0, channel=channel,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(sess)
    db.commit()
    return sess
