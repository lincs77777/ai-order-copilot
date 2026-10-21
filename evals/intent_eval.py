"""意图分流评测（S11 验收）：30 条样句，目标准确率 ≥95%。

可独立执行：python -m evals.intent_eval
"""
from __future__ import annotations

import asyncio

SAMPLES: list[tuple[str, str]] = [
    # ordering × 10
    ("来一份宫保鸡丁", "ordering"),
    ("帮我点一杯珍珠奶茶", "ordering"),
    ("加两个鸡翅到购物车", "ordering"),
    ("我要下单", "ordering"),
    ("来杯冰美式少糖", "ordering"),
    ("再来一份薯条", "ordering"),
    ("把蛋糕加进购物车", "ordering"),
    ("点个外卖，不要辣", "ordering"),
    ("你们的招牌菜是什么？来一个", "ordering"),
    ("结账吧", "ordering"),
    # faq × 10
    ("你们几点营业？", "faq"),
    ("门店地址在哪？", "faq"),
    ("配送费多少钱？", "faq"),
    ("有没有含花生的菜？", "faq"),
    ("微辣辣不辣？", "faq"),
    ("现在有什么优惠活动？", "faq"),
    ("可以外卖吗？", "faq"),
    ("支持什么支付方式？", "faq"),
    ("客服电话多少？", "faq"),
    ("这个蛋糕含不含麸质？", "faq"),
    # rejected × 10
    ("今天天气怎么样？", "rejected"),
    ("讲个笑话听听", "rejected"),
    ("帮我写一篇作文", "rejected"),
    ("你觉得股票行情怎么样？", "rejected"),
    ("最近有什么好看的电影？", "rejected"),
    ("1+1等于几？", "rejected"),
    ("你会唱歌吗？", "rejected"),
    ("给我讲个鬼故事", "rejected"),
    ("帮我订去福州的火车票", "rejected"),
    ("推荐几本好书", "rejected"),
]


async def main() -> None:
    from app.agent.router import classify, rule_classify

    correct = 0
    misses: list[tuple[str, str, str, str]] = []
    rule_hits = 0
    for msg, expected in SAMPLES:
        source = rule_classify(msg)[1]
        if source == "rule":
            rule_hits += 1
        got = await classify(msg)
        mark = "✓" if got == expected else "✗"
        if got == expected:
            correct += 1
        else:
            misses.append((msg, expected, got, source))
        print(f"{mark} [{source:>5}] {msg}  → {got}")

    n = len(SAMPLES)
    print(f"\naccuracy: {correct}/{n} = {correct / n:.0%} (rule hit rate {rule_hits}/{n})")
    if misses:
        print("misses:")
        for m in misses:
            print("  ", m)
    if correct / n < 0.95:
        raise SystemExit(1)
    print("intent eval: PASS")


if __name__ == "__main__":
    asyncio.run(main())
