"""工具层基类：ToolContext / ToolResult（设计文档 §3 tools/base.py）。

ToolResult 统一返回、不抛异常——工具执行失败以结构化错误回喂模型自我纠正。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from app.core.errors import AppError


@dataclass
class ToolContext:
    """一次对话轮次的工具执行上下文。"""
    user_token: str          # 原始用户 token（透传用）
    member_id: int
    shop_id: int | None
    session_id: str = ""     # 会话购物车归属


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error_code: str | None = None
    user_message: str | None = None          # 给模型回喂/给用户看的话术
    summary: dict[str, Any] = field(default_factory=dict)  # SSE tool 事件摘要

    @classmethod
    def success(cls, data: Any = None, **summary) -> "ToolResult":
        return cls(ok=True, data=data, summary=summary)

    @classmethod
    def fail(cls, error_code: str, user_message: str, data: Any = None) -> "ToolResult":
        return cls(ok=False, data=data, error_code=error_code, user_message=user_message)

    @classmethod
    def from_error(cls, e: "AppError") -> "ToolResult":
        """把 AppError（上游超时/商品不存在等）转为不抛异常的失败结果。"""
        return cls(ok=False, error_code=e.code, user_message=e.user_message, data=e.detail)


ToolHandler = Callable[..., Awaitable["ToolResult"]]
