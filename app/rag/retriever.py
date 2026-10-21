"""混合检索：向量 top8 + BM25 → RRF 融合 → 相关性阈值过滤（设计文档 §4.2 / §7.5）。"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

from app.rag import embedding, store

RRF_K = 60
SCORE_THRESHOLD = 0.3  # 向量余弦相似度阈值（归一化后）
TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")


@dataclass
class Hit:
    doc_id: str
    text: str
    metadata: dict
    score: float  # 归一化融合分 0~1


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


class _BM25:
    """轻量 BM25（中文按单字 + 英数词切分），内存索引，文档量小够用。"""

    def __init__(self, corpus: dict[str, str]):
        self.doc_ids = list(corpus)
        self.doc_tf: dict[str, dict[str, int]] = {}
        self.df: dict[str, int] = {}
        self.doc_len: dict[str, int] = {}
        for did, text in corpus.items():
            tokens = _tokenize(text)
            self.doc_len[did] = len(tokens) or 1
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self.doc_tf[did] = tf
            for t in tf:
                self.df[t] = self.df.get(t, 0) + 1
        self.avgdl = sum(self.doc_len.values()) / max(len(self.doc_ids), 1)

    def search(self, query: str, top_k: int = 8) -> dict[str, float]:
        k1, b = 1.5, 0.75
        n = len(self.doc_ids)
        scores: dict[str, float] = {}
        q_tokens = _tokenize(query)
        for did in self.doc_ids:
            score = 0.0
            for t in set(q_tokens):
                f = self.doc_tf[did].get(t, 0)
                if f == 0:
                    continue
                idf = math.log(1 + (n - self.df.get(t, 0) + 0.5) / (self.df.get(t, 0) + 0.5))
                score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * self.doc_len[did] / self.avgdl))
            if score > 0:
                scores[did] = score
        ranked = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k])
        return ranked


@lru_cache(maxsize=8)
def _bm25_index(cache_key: str) -> _BM25:
    """BM25 索引缓存（cache_key 用文档数量+首 id 组成，ingest 后自然失效）。"""
    ids, metas = store.all_documents()
    docs = store.get_collection().get(ids=ids, include=["documents"])
    corpus = dict(zip(docs["ids"], docs["documents"]))
    return _BM25(corpus)


def invalidate_index() -> None:
    _bm25_index.cache_clear()


async def retrieve(query: str, top_k: int = 8, filter_off_shelf: bool = True) -> list[Hit]:
    """混合检索。返回归一化融合分 ≥ 阈值的命中列表。"""
    # ① 向量检索
    q_emb = (await embedding.embed([query]))[0]
    res = store.query(q_emb, top_k=top_k)
    ids = res["ids"][0]
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    vec_rank: dict[str, float] = {}
    vec_cos: dict[str, float] = {}
    for did, doc, meta, dist in zip(ids, docs, metas, dists):
        cos = 1.0 - dist  # cosine space: distance = 1 - cosine
        vec_rank[did] = cos
        vec_cos[did] = cos

    # ② BM25 检索
    n_docs = store.count()
    bm = _bm25_index(f"{n_docs}:{ids[0] if ids else ''}") if n_docs else None
    bm_rank = bm.search(query, top_k=top_k) if bm else {}

    # ③ RRF 融合
    def rrf(ranking_lists: list[dict[str, float]]) -> dict[str, float]:
        fused: dict[str, float] = {}
        for lst in ranking_lists:
            for rank, did in enumerate(sorted(lst, key=lambda k: lst[k], reverse=True)):
                fused[did] = fused.get(did, 0.0) + 1.0 / (RRF_K + rank + 1)
        return fused

    fused = rrf([vec_rank, bm_rank])
    if not fused:
        return []
    top_score = max(fused.values())
    hits = []
    for did, score in fused.items():
        normalized = score / top_score
        cos = vec_cos.get(did, 0.0)
        if cos < SCORE_THRESHOLD:
            continue
        idx = ids.index(did)
        meta = metas[idx]
        if filter_off_shelf and meta.get("source") == "menu" and meta.get("is_show") != 1:
            continue
        hits.append(Hit(doc_id=did, text=docs[idx], metadata=meta, score=normalized))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits
