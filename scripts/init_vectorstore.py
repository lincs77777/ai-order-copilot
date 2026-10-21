"""初始化向量库（全量导入 + 过敏原语料覆盖度报告）。

执行：python -m scripts.init_vectorstore --shop-id 2
"""
from __future__ import annotations

import argparse
import asyncio

from app.core.config import get_settings
from app.rag import ingest, retriever, store


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shop-id", type=int, default=int((get_settings().DEV_MOCK_UPSTREAM_TOKEN or "test0")[4:] or 0) or None)
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    s = get_settings()
    shop_id = args.shop_id or 2
    token = args.token or s.DEV_MOCK_UPSTREAM_TOKEN
    if not token:
        raise SystemExit("需要 --token 或配置 DEV_MOCK_UPSTREAM_TOKEN")

    print(f"ingesting shop {shop_id} ...")
    stats = await ingest.ingest_all(shop_id, token)
    print("stats:", stats, "| collection count:", store.count())

    # 过敏原语料覆盖度报告：库内 allergen 来源文档提到的商品名 vs 在售商品
    menu_names: set[str] = set()
    ids, metas = store.all_documents()
    docs = store.get_collection().get(ids=ids, include=["documents", "metadatas"])
    allergen_text = ""
    for did, meta, text in zip(docs["ids"], docs["metadatas"], docs["documents"]):
        if meta.get("source") == "menu":
            first = text.splitlines()[0] if text else ""
            if first.startswith("商品："):
                menu_names.add(first.replace("商品：", "").strip())
        elif meta.get("source") == "allergen":
            allergen_text += text
    covered = {n for n in menu_names if n and n in allergen_text}
    missing = sorted(menu_names - covered)
    print(f"\n过敏原语料覆盖度: {len(covered)}/{len(menu_names)}")
    if missing:
        print("缺口语料（需运营补录，缺口的商品涉过敏原问题时走保守话术）：")
        for n in missing:
            print("  -", n)

    # 检索冒烟
    hits = await retriever.retrieve("有没有不含花生的饮料？")
    print(f"\nretrieval smoke: {len(hits)} hits")
    for h in hits[:3]:
        print(f"  [{h.score:.2f}] {h.doc_id}: {h.text[:60]!r}")


if __name__ == "__main__":
    asyncio.run(main())
