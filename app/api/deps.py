"""FastAPI 依赖注入：当前用户上下文、限流器、IP 提取（设计文档 §8.1/§8.4）。"""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request

from app.core.errors import AppError, ErrorCode
from app.core.ratelimit import RateLimiter
from app.core.security import UserContext, verify_token


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return RateLimiter()


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth:
        raise AppError(*ErrorCode.UNAUTHORIZED, http_status=401)
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AppError(*ErrorCode.UNAUTHORIZED, http_status=401)
    return parts[1].strip()


def get_client_ip(request: Request) -> str:
    """优先取反代真实 IP（X-Forwarded-For 首个），回落直连 IP。"""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_current_user(request: Request) -> UserContext:
    """鉴权依赖：Bearer token → UserContext（无效/过期 401）。"""
    token = _extract_bearer_token(request)
    ctx = verify_token(token)
    # 透传场景需要原 token：挂到 request.state 供工具层使用
    request.state.user_token = token
    return ctx


def enforce_rate_limits(request: Request, user: UserContext) -> None:
    """限流依赖逻辑（在 chat 等入口显式调用，避免健康检查被误限）。"""
    rl = get_rate_limiter()
    rl.check_user(user.member_id)
    rl.check_ip(get_client_ip(request))


def require_user_with_limits(request: Request) -> UserContext:
    """鉴权 + 限流组合依赖。"""
    user = get_current_user(request)
    enforce_rate_limits(request, user)
    return user


# 常用别名
CurrentUser = Depends(get_current_user)
