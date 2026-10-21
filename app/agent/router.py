"""意图分流（设计文档 §7.3）：点餐 ordering / 知识问答 faq / 闲聊拒绝 rejected。

规则优先（高置信词表，零成本零延迟）；不置信时用轻量模型三分类。
分流结果决定工具白名单（faq 无写工具，rejected 无工具）。
"""
from __future__ import annotations

import json
import re

from app.core.config import get_settings
from app.core.errors import AppError

INTENT_ORDERING = "ordering"
INTENT_FAQ = "faq"
INTENT_REJECTED = "rejected"

# ---------- 强规则（高精确度词表） ----------

_ORDERING_STRONG: list[re.Pattern] = [
    re.compile(p) for p in [
        r"来(一|两|三|几|半)?(份|个|杯|瓶|碗)",
        r"(帮我?|给我?) (点|加|来|买)",
        r"(加购|加入购物车|购物车)",
        r"(点餐|点单|下单|结账|结帐|买单|付款)",
        r"(我要?|想) ?(点|买|吃|喝)[^？?]{0,12}(商品|菜|饭|面|奶茶|咖啡|饮料|蛋糕|鸡排)",
        r"(再来|再来一份|续)",
    ]
]

_FAQ_STRONG: list[re.Pattern] = [
    re.compile(p) for p in [
        r"(营业时间|开门|关门|打烊|几点开|几点关|几点休)",
        r"(门店)?(地址|在哪|位置|怎么走|导航)",
        r"(电话|联系方式|客服电话)",
        r"(配送费|运费|外卖费|起送|免配)",
        r"(过敏|成分|配料|原料|含不含|有没有花生|有没有海鲜|有没有蛋|乳糖|麸质)",
        r"(取餐|出餐|多久能取|多久能拿到|多久可以取|什么时候能取)",
        r"(辣不辣|微辣|中辣|特辣|不辣)",
        r"(优惠|活动|折扣|满减|会员|积分|储值)",
        r"(支持什么|怎么)支付",
        r"(能外卖|可以外卖|送外卖|外送)",
    ]
]

_REJECTED_STRONG: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"(讲个|说个|来个)(笑话|故事|段子)",
        r"(今天|明天)?天气(怎么样|如何)",
        r"(写|帮我写)(一段|个|篇)(代码|作文|诗|文章)",
        r"(特朗普|拜登|普京|台独|港独)",
        r"(股票|基金|比特币|炒币)\s*(怎么|行情|推荐)",
    ]
]


def rule_classify(message: str) -> tuple[str | None, str]:
    """返回 (intent | None, 来源)。None = 规则不置信，交给模型。"""
    for pat in _REJECTED_STRONG:
        if pat.search(message):
            return INTENT_REJECTED, "rule"
    for pat in _FAQ_STRONG:
        if pat.search(message):
            return INTENT_FAQ, "rule"
    for pat in _ORDERING_STRONG:
        if pat.search(message):
            return INTENT_ORDERING, "rule"
    return None, "none"


# ---------- 轻量模型分类 ----------

_CLASSIFY_PROMPT = (
    "你是餐饮点餐系统的意图分类器。把用户消息分为三类之一：\n"
    "- ordering：点餐/加购/改单/下单/询问具体菜品并带购买意向\n"
    "- faq：门店咨询（营业时间、地址、配送、优惠、过敏原、成分等）\n"
    "- rejected：与点餐和门店无关的闲聊或其他话题\n"
    '只输出 JSON：{{"intent": "ordering|faq|rejected"}}\n\n'
    "用户消息：{message}"
)


async def model_classify(message: str) -> str:
    """轻量模型三分类；失败回落 ordering（宁可当点餐处理）。"""
    from app.llm import gateway

    s = get_settings()
    messages = [{"role": "user", "content": _CLASSIFY_PROMPT.format(message=message[:200])}]
    try:
        collected = ""
        async for kind, payload in gateway.stream_chat(
            messages, tools=None, model=s.LLM_MODEL_LIGHT, temperature=0.0,
        ):
            if kind == "delta":
                collected += payload
            elif kind == "finish":
                break
        m = re.search(r'"intent"\s*:\s*"(ordering|faq|rejected)"', collected)
        return m.group(1) if m else INTENT_ORDERING
    except AppError:
        return INTENT_ORDERING


async def classify(message: str) -> str:
    """入口：规则优先，不置信走轻量模型。"""
    intent, source = rule_classify(message)
    if intent is not None:
        return intent
    return await model_classify(message)


REJECT_REPLY = "我是本店点餐助手，只能帮您点餐和解答门店相关问题哦～ 想看看菜单还是直接点单？"
