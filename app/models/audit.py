"""审计与订单确认态 ORM（设计文档 §6：ai_tool_audit / ai_order_preview / ai_feedback）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base


class AiToolAudit(Base):
    __tablename__ = "ai_tool_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    args_digest: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 脱敏后的参数摘要
    upstream_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    upstream_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(512), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(64), nullable=True)  # HMAC 签名（防篡改）
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = ({"comment": "工具调用审计表（合规回溯，保留≥180天）"},)


class AiOrderPreview(Base):
    __tablename__ = "ai_order_preview"

    preview_id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cart_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)  # compute 入参草稿快照
    amount_detail: Mapped[dict] = mapped_column(JSON, nullable=False)  # Java compute 返回的金额明细（权威）
    amount_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending/confirmed/expired
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = ({"comment": "订单确认态表（preview 幂等）"},)


class AiFeedback(Base):
    __tablename__ = "ai_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    reply_id: Mapped[str] = mapped_column(String(64), nullable=False)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rating: Mapped[str] = mapped_column(String(8), nullable=False)  # up/down
    comment: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = ({"comment": "用户反馈表（评测数据回流）"},)


class AiTrace(Base):
    """每轮对话一条完整链路记录（设计文档 §9，可按会员/时间回放）。"""
    __tablename__ = "ai_trace"

    trace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    intent: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    user_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tool_calls: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON
    retrieval: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON
    rounds: Mapped[str | None] = mapped_column(Text, nullable=True)       # JSON
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finish_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = ({"comment": "每轮对话全链路 trace（保留30天）"},)
