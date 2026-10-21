"""Prometheus 指标（设计文档 §9）。

指标命名与文档对应：
  chat_requests_total / chat_duration_seconds / intent_total
  tool_calls_total{tool,status} / llm_calls_total / llm_duration_seconds
  tokens_total{kind} / fallback_total{reason} / injection_blocked_total
  order_preview_total / order_confirmed_total
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

LABEL_INTENT = ("intent",)

# 对话
chat_requests_total = Counter("chat_requests_total", "对话请求数", ["intent", "channel"])
chat_duration_seconds = Histogram(
    "chat_duration_seconds", "单轮对话耗时",
    buckets=(0.5, 1, 2, 3, 5, 8, 13, 21, 30, 60),
)

# LLM
llm_calls_total = Counter("llm_calls_total", "LLM 调用次数", ["model"])
llm_duration_seconds = Histogram(
    "llm_duration_seconds", "LLM 调用耗时",
    buckets=(0.3, 0.6, 1, 1.5, 2, 3, 5, 8, 13, 30),
)
tokens_total = Counter("tokens_total", "Token 消耗", ["kind"])  # prompt/completion

# 工具
tool_calls_total = Counter("tool_calls_total", "工具调用次数", ["tool", "status"])

# 降级与安全
fallback_total = Counter("fallback_total", "降级次数", ["reason"])  # circuit_open/tool_fail/round_limit/llm_error
injection_blocked_total = Counter("injection_blocked_total", "注入拦截次数", [])

# 业务转化
order_preview_total = Counter("order_preview_total", "订单预览次数", [])
order_confirmed_total = Counter("order_confirmed_total", "订单确认次数", ["result"])

# 检索
retrieval_hits = Histogram("retrieval_hits", "RAG 检索命中数", buckets=(0, 1, 2, 3, 5, 8))
