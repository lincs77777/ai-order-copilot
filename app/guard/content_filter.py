"""输出合规过滤（设计文档 §8.2 第四道防线 / §8.3）。

命中：系统提示词片段泄露 / 内部 URL / 越权数据特征 / 敏感词 → AI_1002 拦截替换。
"""
from __future__ import annotations

import re

# 系统提示词泄露特征
_LEAK_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"system\s*prompt\s*[:：]",
        r"你是餐饮门店的 ai 点餐助手",
        r"核心规则（必须遵守）",
        r"JAVA_BASE_URL|LLM_API_KEY|REDIS_URL|sk-[a-zA-Z0-9]{8,}",
    ]
]

# 内部地址
_INTERNAL_URL = re.compile(r"(https?://)?(127\.0\.0\.1|localhost|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)[:/\d]*", re.IGNORECASE)

# 敏感词（政治/违禁/竞品导流——示例集，生产接词库服务）
_SENSITIVE_WORDS = ["政治敏感词示例", "竞品A点餐", "竞品B点餐"]

SAFE_REPLY = "抱歉，我的回复涉及了不该出现的内容，已为你过滤。有什么可以帮您点的吗？"


def filter_output(text: str) -> tuple[bool, str | None]:
    """返回 (违规, 违规类型)。命中 → 上层用 SAFE_REPLY 替换并记 AI_1002。"""
    for pat in _LEAK_PATTERNS:
        if pat.search(text):
            return True, "prompt_leak"
    if _INTERNAL_URL.search(text):
        return True, "internal_url"
    for w in _SENSITIVE_WORDS:
        if w in text:
            return True, "sensitive_word"
    return False, None
