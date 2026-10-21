"""菜单知识同步：定时轮询 diff（设计文档 §7.5，MQ 方案为演进项）。

- 默认 5 分钟轮询 /app-api/product/products，与库内菜单文档 hash 比对
- 变更 → ingest_all 增量重嵌入；无变化 → 跳过
- 由 main.py lifespan 启动为 asyncio 后台任务
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

from app.core.config import get_settings
from app.core.java_client import get_java_client
from app.rag import ingest, retriever, store

log = logging.getLogger(__name__)

_task: asyncio.Task | None = None
_last_sigs: dict[int, str] = {}


def _menu_signature(payload: Any) -> str:
    return hashlib.md5(str(payload).encode("utf-8")).hexdigest()


async def sync_once(shop_id: int, token: str) -> bool:
    """执行一次同步。返回是否发生变更。进程重启后首轮总会重建一次（幂等）。"""
    data = get_java_client().get_sync("/app-api/product/products", token=token,
                                      params={"shopId": shop_id})
    sig = _menu_signature(data)
    if sig == _last_sigs.get(shop_id):
        return False
    _last_sigs[shop_id] = sig
    await ingest.ingest_all(shop_id, token)
    retriever.invalidate_index()
    log.info("menu sync: changed, sig=%s", sig[:8])
    return True


async def _loop(shop_id: int, token: str) -> None:
    interval = 300
    while True:
        try:
            changed = await sync_once(shop_id, token)
            if changed:
                log.info("RAG sync: menu updated and re-ingested")
        except Exception as e:  # 轮询永不中断
            log.warning("RAG sync failed: %s", e)
        await asyncio.sleep(interval)


def start(shop_id: int, token: str) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(shop_id, token))


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
