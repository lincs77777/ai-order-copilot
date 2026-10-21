"""token 校验适配器（设计文档 §8.1）。

方案A（默认，AUTH_MODE=redis）：直读 yshop Redis，key `oauth2_access_token:{token}`，
  值为 OAuth2AccessTokenDO JSON（字段已对照 Java 源码核实：userId/userType/userInfo/
  clientId/scopes/expiresTime/shopId/tenantId），带 30s 本地缓存。
方案B（AUTH_MODE=introspection）：委托 Java `/app-api/ai/auth/verify`——该接口尚未实现，
  实现前切到此模式会抛配置错误。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import redis as redis_lib

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.redis import get_yshop_redis


@dataclass(frozen=True)
class UserContext:
    member_id: int
    shop_id: int | None
    user_type: int | None
    tenant_id: int | None
    token: str


def _parse_expires_time(raw) -> datetime | None:
    """兼容 Jackson LocalDateTime 的多种序列化形态：
    ISO 字符串 / "yyyy-MM-dd HH:mm:ss" / [y,M,d,H,m,s] 数组 / epoch 毫秒。
    LocalDateTime 无时区，按服务器本地时间（GMT+8）比较。
    """
    if raw is None:
        return None
    if isinstance(raw, list) and len(raw) >= 3:
        parts = (list(raw) + [0] * 6)[:6]
        return datetime(*map(int, parts))
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000 if raw > 1e11 else raw)
    if isinstance(raw, str):
        s = raw.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s[:19].replace("T", " ") if "T" in s else s[:19], fmt)
            except ValueError:
                continue
    return None


class _TokenCache:
    """进程内 token 校验缓存（默认 30s），避免每请求打 Redis。"""

    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._data: dict[str, tuple[UserContext | None, float]] = {}
        self._lock = threading.Lock()

    def get(self, token: str) -> tuple[UserContext | None, bool]:
        """返回 (ctx, hit)；ctx 可能为 None 表示"缓存过无效结论"。"""
        with self._lock:
            item = self._data.get(token)
            if not item:
                return None, False
            ctx, ts = item
            if time.monotonic() - ts > self._ttl:
                del self._data[token]
                return None, False
            return ctx, True

    def put(self, token: str, ctx: UserContext | None) -> None:
        with self._lock:
            if len(self._data) > 10_000:  # 防膨胀
                self._data.clear()
            self._data[token] = (ctx, time.monotonic())


class RedisTokenVerifier:
    """方案A：直读 yshop Redis。"""

    KEY_TEMPLATE = "oauth2_access_token:{}"

    def __init__(self, cache_ttl: int):
        self._client: redis_lib.Redis = get_yshop_redis()
        self._cache = _TokenCache(cache_ttl)

    def verify(self, token: str) -> UserContext:
        ctx, hit = self._cache.get(token)
        if hit:
            return self._raise_or_return(ctx)

        raw = self._client.get(self.KEY_TEMPLATE.format(token))
        ctx = self._deserialize(raw)
        self._cache.put(token, ctx)
        return self._raise_or_return(ctx)

    @staticmethod
    def _raise_or_return(ctx: UserContext | None) -> UserContext:
        if ctx is None:
            raise AppError(*ErrorCode.UNAUTHORIZED, http_status=401)
        return ctx

    @staticmethod
    def _deserialize(raw: str | None) -> UserContext | None:
        if not raw:
            return None
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return None
        expires = _parse_expires_time(d.get("expiresTime"))
        if expires is not None and expires <= datetime.now():
            return None  # 已过期
        user_id = d.get("userId")
        if user_id is None:
            return None
        return UserContext(
            member_id=int(user_id),
            shop_id=int(d["shopId"]) if d.get("shopId") is not None else None,
            user_type=d.get("userType"),
            tenant_id=d.get("tenantId"),
            token="",  # 上下文不携带原 token，透传场景由调用方传入
        )


class IntrospectionTokenVerifier:
    """方案B：委托 Java introspection（/app-api/ai/auth/verify 待实现，见 S4 备注）。"""

    def __init__(self, java_base_url: str, cache_ttl: int):
        self._base_url = java_base_url.rstrip("/")
        self._cache = _TokenCache(cache_ttl)

    def verify(self, token: str) -> UserContext:
        raise AppError(
            "AI_0005",
            "introspection 模式依赖 Java 侧 /app-api/ai/auth/verify 接口（尚未实现），请使用 AUTH_MODE=redis",
            http_status=500,
        )


_VERIFIER = None


def get_verifier():
    global _VERIFIER
    if _VERIFIER is None:
        s = get_settings()
        if s.AUTH_MODE == "redis":
            _VERIFIER = RedisTokenVerifier(s.AUTH_CACHE_TTL_SECONDS)
        elif s.AUTH_MODE == "introspection":
            _VERIFIER = IntrospectionTokenVerifier(s.JAVA_BASE_URL, s.AUTH_CACHE_TTL_SECONDS)
        else:
            raise AppError("AI_0005", f"未知 AUTH_MODE: {s.AUTH_MODE}", http_status=500)
    return _VERIFIER


def verify_token(token: str) -> UserContext:
    """对外入口：校验 opaque token，无效/过期抛 401。"""
    return get_verifier().verify(token)
