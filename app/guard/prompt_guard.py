"""Prompt 注入检测与输入消毒（设计文档 §8.2 第一道防线）。

高置信特征命中 → 直接拒绝（AI_1001，不进 LLM，零成本）；
低置信不拦截，交由指令分层（system prompt）+ 执行侧兜底。
"""
from __future__ import annotations

import re

# 高置信注入特征（正则，不区分大小写）
_HIGH_CONFIDENCE_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"忽略(之前|以上|上面|前面)(的|所有)?(指令|提示|规则|设定)",
        r"ignore (all |any )?(previous|above|prior|earlier) (instructions|prompts|rules)",
        r"disregard (all |any )?(previous|above) (instructions|rules)",
        r"(你现在是|你现在扮演|从现在开始你是|假设你是|假装你是)[^。！？]{0,20}(助手|角色|ai|AI|系统|机器人)",
        r"(你现在是|你现在扮演)\s*(另一个|新的|不同)",
        r"you are now (a|an|the)?\s*(different|new|dan|jailbreak| unrestricted)",
        r"(系统提示词|system prompt|初始指令|初始提示).{0,10}(是什么|告诉我|输出|打印|泄露|显示)",
        r"(输出|打印|复述|重复)(一下)?(你的|系统的)?(系统提示|system prompt|初始指令|规则设定)",
        r"(越狱|jailbreak|DAN模式|开发者模式)",
        r"(告诉我|告诉我吧)\s*(你的)?(秘密|密钥|api\s*key|密码)",
        r"</?(system|assistant|tool)_?prompt>",        # 伪造系统标签
        r"<\|im_start\|>|<\|endoftext\|>",             # chatml 越权标记
        r"^\s*(system|assistant|developer)\s*[:：]",   # 伪角色前缀授权话术
    ]
]

# 低置信特征：仅做输入消毒（转义包裹），不拒绝
_SANITIZE_PATTERNS: list[re.Pattern] = [
    re.compile(r"(请?忽略.{0,6}指令)", re.IGNORECASE),
]


def detect_injection(message: str) -> tuple[bool, str | None]:
    """返回 (命中, 命中模式说明)。高置信命中 → 上层直接拒绝。"""
    for pat in _HIGH_CONFIDENCE_PATTERNS:
        m = pat.search(message)
        if m:
            return True, pat.pattern
    return False, None


def sanitize(message: str) -> str:
    """低置信消毒：不做语义改写，仅对可疑片段加引号隔离标记。"""
    for pat in _SANITIZE_PATTERNS:
        message = pat.sub(lambda m: f"『{m.group(1)}』", message)
    return message
