"""Embedding API 客户端（硅基流动/阿里，OpenAI 兼容 /embeddings）。"""
from __future__ import annotations

import asyncio
from functools import lru_cache

import httpx

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode


@lru_cache
def _settings() -> tuple[str, str, str]:
    s = get_settings()
    return s.EMBEDDING_BASE_URL.rstrip("/"), s.EMBEDDING_API_KEY, s.EMBEDDING_MODEL


async def embed(texts: list[str], retries: int = 2) -> list[list[float]]:
    """批量向量化。失败抛 AppError（AI_3004）。"""
    base, key, model = _settings()
    if not key or key == "sk-xxx":
        raise AppError("AI_3004", "Embedding API Key 未配置", http_status=500)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{base}/embeddings",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model, "input": texts},
                )
            r.raise_for_status()
            data = r.json()["data"]
            # 按 index 还原顺序
            return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(0.5 * (attempt + 1))
    raise AppError("AI_3004", "向量化服务不可用", http_status=502, detail=str(last_exc))
