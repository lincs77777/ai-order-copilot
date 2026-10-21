"""Chroma 向量库封装（dev 内嵌 PersistentClient；生产切 Milvus——adapter 预留）。"""
from __future__ import annotations

from functools import lru_cache

import chromadb

from app.core.config import get_settings

COLLECTION = "copilot_knowledge"


@lru_cache
def get_collection():
    s = get_settings()
    client = chromadb.PersistentClient(path=s.CHROMA_PERSIST_DIR)
    return client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def upsert(ids: list[str], documents: list[str], embeddings: list[list[float]],
           metadatas: list[dict]) -> None:
    get_collection().upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)


def delete_where(where: dict) -> None:
    get_collection().delete(where=where)


def count() -> int:
    return get_collection().count()


def query(embedding: list[float], top_k: int = 8, where: dict | None = None):
    return get_collection().query(
        query_embeddings=[embedding], n_results=top_k, where=where,
        include=["documents", "metadatas", "distances"],
    )


def all_documents() -> tuple[list[str], list[dict]]:
    """全量拉取（轮询 diff 用；dev 规模小可行，生产换 Milvus 后用标量过滤）。"""
    got = get_collection().get(include=["metadatas"])
    return got["ids"], got["metadatas"]
