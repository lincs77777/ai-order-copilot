"""会话历史与用户反馈接口（设计文档 §5.1）。

GET  /api/v1/session/{id}/messages  历史消息（分页，仅本人）
POST /api/v1/feedback               点赞/点踩（评测数据回流）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import UserContext, get_current_user
from app.core.errors import AppError, ErrorCode
from app.models.audit import AiFeedback
from app.models.db import get_db
from app.models.session import AiMessage, AiSession

router = APIRouter()


@router.get("/session/{session_id}/messages")
def session_messages(session_id: str,
                     page: int = Query(1, ge=1),
                     size: int = Query(20, ge=1, le=100),
                     user: UserContext = Depends(get_current_user),
                     db: OrmSession = Depends(get_db)):
    """历史消息分页（按用户鉴权，只能查自己的会话）。"""
    sess = db.get(AiSession, session_id)
    if sess is None or sess.member_id != user.member_id:
        raise AppError(*ErrorCode.FORBIDDEN, http_status=403, detail="会话不属于当前用户")

    q = (db.query(AiMessage)
         .filter(AiMessage.session_id == session_id,
                 AiMessage.role.in_(["user", "assistant"]))
         .order_by(AiMessage.id.asc()))
    total = q.count()
    rows = q.offset((page - 1) * size).limit(size).all()
    return {
        "session_id": session_id,
        "total": total,
        "page": page,
        "size": size,
        "items": [{
            "id": r.id, "role": r.role, "content": r.content,
            "intent": r.intent, "created_at": r.created_at.isoformat(timespec="seconds"),
        } for r in rows],
    }


class FeedbackRequest(BaseModel):
    reply_id: str
    rating: str          # up / down
    comment: str | None = None


@router.post("/feedback")
def feedback(body: FeedbackRequest, user: UserContext = Depends(get_current_user),
             db: OrmSession = Depends(get_db)):
    """用户反馈（评测数据回流）。"""
    if body.rating not in ("up", "down"):
        raise AppError(*ErrorCode.PARAM_INVALID, http_status=400, detail="rating 仅支持 up/down")
    db.add(AiFeedback(reply_id=body.reply_id, member_id=user.member_id,
                      rating=body.rating, comment=(body.comment or "")[:512]))
    db.commit()
    return {"ok": True}
