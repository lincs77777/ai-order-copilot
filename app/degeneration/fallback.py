"""降级与熔断（设计文档 §7.4）。

- CircuitBreaker：LLM 网关 60s 窗口连续 3 次超时/5xx → 熔断 60s
- rule_menu_recommendation：熔断期间的规则推荐（销量 TopN，数据仍来自 Java）
"""
from __future__ import annotations

import time
from collections import deque

from app.tools.base import ToolContext


class CircuitBreaker:
    def __init__(self, threshold: int = 3, window_seconds: float = 60.0, open_seconds: float = 60.0):
        self.threshold = threshold
        self.window = window_seconds
        self.open_seconds = open_seconds
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None

    def record_failure(self) -> None:
        now = time.monotonic()
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.window:
            self._failures.popleft()
        if len(self._failures) >= self.threshold:
            self._opened_at = now

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.open_seconds:
            self._opened_at = None  # 半开：放行试探
            self._failures.clear()
            return False
        return True


_breaker = CircuitBreaker()


def get_breaker() -> CircuitBreaker:
    return _breaker


async def rule_menu_recommendation(ctx: ToolContext, top_n: int = 5) -> str:
    """规则降级话术：销量 TopN 推荐（数据来自 Java 菜单接口；Java 也不可用时为纯道歉话术）。"""
    apology = "抱歉，AI 点餐助手暂时不可用，已为您切换传统点单方式，给您带来不便敬请谅解。"
    try:
        from app.tools import menu_tools
        index = await menu_tools._fetch_product_index(ctx)
        items = [it for it in index.values() if it.get("is_show") == 1]
        items.sort(key=lambda it: it.get("sales") or 0, reverse=True)
        if not items:
            return apology
        rows = "\n".join(f"{i + 1}. {it['store_name']}（¥{it['price']}）" for i, it in enumerate(items[:top_n]))
        return (f"抱歉，AI 点餐助手暂时不可用。本店热销推荐：\n{rows}\n"
                f"您可以直接在菜单页下单，稍后再来找我聊天～")
    except Exception:
        return apology
