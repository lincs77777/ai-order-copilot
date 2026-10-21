"""Redis 限流器（设计文档 §8.4）。

- 用户级：N 次/分钟 + N 次/天
- IP 级：N 次/分钟
- 单会话：并发 1 路生成（分布式锁，重复请求返回进行中）

实现：Lua 令牌桶（原子，免竞态）；日配额用固定窗口计数。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import redis as redis_lib

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.redis import get_redis

# 令牌桶：KEYS[1]=桶key, ARGV: capacity, refill_per_sec, now_micro, requested
_TOKEN_BUCKET_LUA = """
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
    tokens = capacity
    ts = now
end
local elapsed = math.max(0, now - ts) / 1e6
tokens = math.min(capacity, tokens + elapsed * refill)
local allowed = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill) + 60)
return allowed
"""

# 日配额：KEYS[1]=key, ARGV: limit, today_expire_seconds, now
_DAILY_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local limit = tonumber(ARGV[1])
local expire = tonumber(ARGV[2])
if current + 1 > limit then
    return 0
end
local v = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], expire)
return v
"""


class RateLimiter:
    def __init__(self, client: redis_lib.Redis | None = None):
        self._client = client or get_redis()
        self._bucket = self._client.register_script(_TOKEN_BUCKET_LUA)
        self._daily = self._client.register_script(_DAILY_LUA)

    def check_user(self, member_id: int) -> None:
        """用户级限流：分钟令牌桶 + 日配额。超限抛 429。"""
        s = get_settings()
        now_micro = int(time.time() * 1e6)
        allowed = self._bucket(
            keys=[f"ai:rl:user:{member_id}"],
            args=[s.RATE_LIMIT_USER_PER_MIN, s.RATE_LIMIT_USER_PER_MIN / 60.0, now_micro, 1],
        )
        if not allowed:
            raise AppError("AI_0006", "请求过于频繁，请稍后再试", http_status=429)
        today = date.today()
        expire = int((datetime(today.year, today.month, today.day) + timedelta(days=1) - datetime.now()).total_seconds())
        left = self._daily(keys=[f"ai:rl:userday:{member_id}:{today.isoformat()}"],
                           args=[s.RATE_LIMIT_USER_PER_DAY, expire, int(time.time())])
        if not left:
            raise AppError("AI_0007", "今日 AI 使用次数已用完，次日恢复", http_status=429)

    def check_ip(self, ip: str) -> None:
        s = get_settings()
        now_micro = int(time.time() * 1e6)
        allowed = self._bucket(
            keys=[f"ai:rl:ip:{ip}"],
            args=[s.RATE_LIMIT_IP_PER_MIN, s.RATE_LIMIT_IP_PER_MIN / 60.0, now_micro, 1],
        )
        if not allowed:
            raise AppError("AI_0006", "请求过于频繁，请稍后再试", http_status=429)

    def try_acquire_session_lock(self, session_id: str, ttl_seconds: int = 120) -> bool:
        """单会话并发 1 路：抢锁成功 True；进行中 False。"""
        # NX + EX 原子设置
        got = self._client.set(f"ai:slock:{session_id}", "1", nx=True, ex=ttl_seconds)
        return bool(got)

    def release_session_lock(self, session_id: str) -> None:
        self._client.delete(f"ai:slock:{session_id}")
