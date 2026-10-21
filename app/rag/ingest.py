"""知识入库：菜单（Java 实时拉取）+ 运营语料（data/knowledge/*.md）切片向量化。

切片：512 字符 / 64 重叠（§7.5）。文档元数据：source / product_id / updated_at / text_hash。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import AppError
from app.core.java_client import get_java_client
from app.rag import embedding, store

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
KNOWLEDGE_DIR = Path(__file__).parent.parent.parent / "data" / "knowledge"


@dataclass
class Doc:
    doc_id: str
    text: str
    metadata: dict[str, Any]


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= CHUNK_SIZE:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _menu_docs(shop_id: int, token: str) -> list[Doc]:
    """菜单 → 文档：每商品一条（名称/分类/描述/价格/规格），可整段切片。"""
    data = get_java_client().get_sync(
        "/app-api/product/products", token=token, params={"shopId": shop_id})
    docs: list[Doc] = []
    for cate in data or []:
        for g in cate.get("goodsList") or []:
            skus = "；".join(f"{k}（¥{v.get('price')}，库存{v.get('stock')}）"
                             for k, v in (g.get("productValue") or {}).items()) or "单规格"
            text = (
                f"商品：{g.get('storeName')}\n分类：{cate.get('name')}\n"
                f"价格：¥{g.get('price')}\n简介：{g.get('storeInfo') or ''}\n"
                f"规格与库存：{skus}\n销量：{g.get('sales')} 库存：{g.get('stock')} "
                f"状态：{'在售' if g.get('isShow') == 1 else '已下架'}"
            )
            for i, chunk in enumerate(chunk_text(text)):
                docs.append(Doc(
                    doc_id=f"product:{g['id']}:{i}",
                    text=chunk,
                    metadata={
                        "source": "menu", "product_id": int(g["id"]),
                        "shop_id": shop_id, "is_show": int(g.get("isShow") or 0),
                        "text_hash": text_hash(chunk),
                    },
                ))
    return docs


def _knowledge_docs() -> list[Doc]:
    """data/knowledge/*.md → 文档（Q/A 与段落级切分）。"""
    docs: list[Doc] = []
    if not KNOWLEDGE_DIR.exists():
        return docs
    for md in sorted(KNOWLEDGE_DIR.glob("*.md")):
        source = "allergen" if "allergen" in md.name else "faq"
        # 按空行分段落块
        blocks = [b.strip() for b in re.split(r"\n\s*\n", md.read_text(encoding="utf-8")) if b.strip()]
        for bi, block in enumerate(blocks):
            for ci, chunk in enumerate(chunk_text(block)):
                docs.append(Doc(
                    doc_id=f"{source}:{md.stem}:{bi}:{ci}",
                    text=chunk,
                    metadata={"source": source, "product_id": -1, "text_hash": text_hash(chunk)},
                ))
    return docs


async def ingest_all(shop_id: int, token: str) -> dict[str, int]:
    """全量重建式入库（先删菜单旧文档再 upsert；语料按 hash 增量）。"""
    docs = _knowledge_docs() + _menu_docs(shop_id, token)
    if not docs:
        return {"upserted": 0}

    # 语料文档按 hash 去重：已在库且 hash 相同 → 跳过（减少 embedding 调用）
    existing_ids, existing_meta = store.all_documents()
    existing_hash = {i: m.get("text_hash") for i, m in zip(existing_ids, existing_meta)}

    todo = [d for d in docs if existing_hash.get(d.doc_id) != d.metadata["text_hash"]]
    # 菜单文档全量重建：删除库中 menu 来源但本轮不存在的
    menu_ids_now = {d.doc_id for d in docs if d.metadata["source"] == "menu"}
    stale_menu = [i for i, m in zip(existing_ids, existing_meta)
                  if m.get("source") == "menu" and i not in menu_ids_now]
    if stale_menu:
        store.get_collection().delete(ids=stale_menu)

    if not todo:
        return {"upserted": 0, "unchanged": len(docs)}

    embeddings = await embedding.embed([d.text for d in todo])
    from datetime import datetime
    store.upsert(
        ids=[d.doc_id for d in todo],
        documents=[d.text for d in todo],
        embeddings=embeddings,
        metadatas=[{**d.metadata, "updated_at": datetime.now().isoformat(timespec="seconds")}
                   for d in todo],
    )
    return {"upserted": len(todo), "unchanged": len(docs) - len(todo)}


__all__ = ["ingest_all", "chunk_text", "text_hash", "Doc", "AppError"]
