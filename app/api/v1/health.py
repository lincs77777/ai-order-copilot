"""存活/就绪探针（设计文档 §5.1）。

S1 阶段：/health 仅存活；/health/ready 尽力探测 Java/Redis/MySQL，
任一不可达标记 degraded（联调期放宽为 200，S3 接入鉴权后收紧语义）。
"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter

from app.core.config import get_settings
from app.core.redis import get_redis

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": get_settings().APP_ENV}


@router.get("/health/ready")
async def ready() -> dict:
    settings = get_settings()
    components: dict[str, dict] = {}

    # Java 业务系统
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(settings.JAVA_BASE_URL + "/", follow_redirects=False)
        ok = resp.status_code < 500
        components["java"] = {"ok": ok, "status_code": resp.status_code,
                              "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        components["java"] = {"ok": False, "error": type(e).__name__,
                              "latency_ms": int((time.monotonic() - t0) * 1000)}

    # Redis
    t0 = time.monotonic()
    try:
        client = get_redis()
        components["redis"] = {"ok": bool(client.ping()),
                               "latency_ms": int((time.monotonic() - t0) * 1000)}
        client.close()
    except Exception as e:  # noqa: BLE001
        components["redis"] = {"ok": False, "error": type(e).__name__,
                               "latency_ms": int((time.monotonic() - t0) * 1000)}

    # 向量库（chroma 内嵌模式在 S12 才启用，此处仅上报配置）
    components["vector_db"] = {"ok": True, "backend": settings.VECTOR_DB_BACKEND,
                               "note": "chroma embedded, lazy init at S12"}

    all_ok = all(c.get("ok", False) for c in components.values())
    return {"status": "ok" if all_ok else "degraded", "components": components}
