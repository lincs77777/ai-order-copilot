"""Redis 客户端工厂。

本机/生产 Redis 为 5.x（不支持 RESP3 的 HELLO 命令），
redis-py >= 6 默认协商 RESP3 会报 unknown command 'HELLO'，
故统一强制 protocol=2。
"""
from __future__ import annotations

import redis

from app.core.config import get_settings


def get_redis() -> redis.Redis:
    """ai-service 自用连接（限流/缓存，db 2）。"""
    return redis.Redis.from_url(
        get_settings().REDIS_URL, socket_timeout=3.0, protocol=2
    )


def get_yshop_redis() -> redis.Redis:
    """方案A 鉴权直读 yshop token 连接（yshop 默认 db 0，key: oauth2_access_token:{token}）。"""
    return redis.Redis.from_url(
        get_settings().YSHOP_REDIS_URL, socket_timeout=3.0, protocol=2
    )
