"""防幻觉守卫（设计文档 §7.1 / §8.2 执行侧兜底）。

- 过敏原"安全断言"检测：模型输出断言"不含某过敏原/绝对安全"→ 命中即替换为保守话术
- 金额复核（供评测/预览路径调用）：LLM 文本金额 vs 工具权威金额
"""
from __future__ import annotations

import re

from app.core.errors import ErrorCode

# 过敏原词表
ALLERGEN_WORDS = ["过敏", "花生", "海鲜", "虾", "蟹", "鱼", "蛋", "乳", "奶", "麸质", " gluten", "大豆", "坚果"]

# "安全断言"特征：与过敏原上下文共现的"不含/没有/无X/不会过敏/绝对安全"
_SAFE_CLAIM_PATTERNS: list[re.Pattern] = [
    re.compile(p) for p in [
        r"(不含|没有|无|零)[^。，,！!？?]{0,6}(过敏原|花生|海鲜|麸质|乳|蛋|大豆|坚果)",
        r"(不会|绝不|不会导致)\s*(引起)?过敏",
        r"(绝对|完全|百分之百)(安全|放心|无过敏)",
        r"(过敏体质|过敏)(也?可以|能)(放心)?(吃|喝|食用)",
    ]
]

CONSERVATIVE_NOTICE = "温馨提示：该商品成分信息待确认，建议咨询店员后再行食用。"


def check_allergen_safety_claim(text: str) -> tuple[bool, str | None]:
    """输出中是否出现"过敏原安全"断言。命中 → 上层替换/追加保守话术。"""
    has_allergen_context = any(w in text for w in ALLERGEN_WORDS)
    if not has_allergen_context:
        return False, None
    for pat in _SAFE_CLAIM_PATTERNS:
        m = pat.search(text)
        if m:
            return True, m.group(0)
    return False, None


def check_amount_consistency(text: str, authoritative_total: str | None) -> tuple[bool, str | None]:
    """LLM 文本中出现的"合计/总共/应付"金额是否与权威金额一致。

    返回 (一致, 文本中声称的金额)。用于 order_preview 之后的输出复核与评测。
    """
    if authoritative_total is None:
        return True, None
    m = re.search(r"(合计|总共|应付|共计|总价)[^0-9。]{0,6}([0-9]+(?:\.[0-9]{1,2})?)\s*元?", text)
    if not m:
        return True, None
    claimed = m.group(2)
    ok = Decimal_eq(claimed, authoritative_total)
    return ok, claimed


def Decimal_eq(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.005
    except ValueError:
        return False


def refusal_for(code_pair: tuple[str, str]) -> str:
    return f"抱歉，{code_pair[1]}。"
