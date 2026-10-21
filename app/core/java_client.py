"""yshop Java 上游 HTTP 客户端（设计文档 §5.2）。

- 透传用户 token（on-behalf-of），Java 侧权限体系原样生效
- X-Internal-Sign：HMAC-SHA256 时间戳签名（防内网伪造；Java 侧校验点待后续补充）
- 统一解包 CommonResult（code==0 成功），超时/上游错误映射 AI_2xxx
"""
from __future__ import annotations

import hashlib
import hmac
import time
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode


def resolve_upstream_token(user_token: str) -> str:
    """dev 联调：yshop 的 mock 机制（test{userId}）与 ai-service 的 Redis 注入 token
    不是同一体系，dev 下统一替换为固定 mock token；生产透传真实 token。"""
    s = get_settings()
    if s.APP_ENV == "dev" and s.DEV_MOCK_UPSTREAM_TOKEN:
        return s.DEV_MOCK_UPSTREAM_TOKEN
    return user_token


def _internal_sign(timestamp: str, method: str, path: str) -> str:
    secret = get_settings().JAVA_INTERNAL_SECRET.encode()
    msg = f"{timestamp}\n{method.upper()}\n{path}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


class JavaClient:
    """异步 httpx 客户端。所有方法在失败时抛 AppError（AI_2xxx）。"""

    def __init__(self):
        s = get_settings()
        self._base_url = s.JAVA_BASE_URL.rstrip("/")
        self._timeout = s.JAVA_TIMEOUT_SECONDS

    def _headers(self, token: str, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time()))
        from app.core.logging import get_request_id
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Internal-Timestamp": ts,
            "X-Internal-Sign": _internal_sign(ts, method, path),
        }
        rid = get_request_id()
        if rid:
            headers["X-Request-Id"] = rid  # 全链路追踪贯穿到 Java
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        headers = self._headers(token, method, path)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(method, url, params=params, json=json_body, headers=headers)
        except httpx.TimeoutException as e:
            raise AppError(*ErrorCode.UPSTREAM_TIMEOUT, http_status=504, detail=f"{method} {path}: {e}")
        except httpx.HTTPError as e:
            raise AppError(*ErrorCode.UPSTREAM_ERROR, http_status=502, detail=f"{method} {path}: {e}")

        if resp.status_code == 401:
            raise AppError(*ErrorCode.UNAUTHORIZED, http_status=401, detail=f"{method} {path}")
        if resp.status_code >= 500:
            raise AppError(*ErrorCode.UPSTREAM_ERROR, http_status=502,
                           detail=f"{method} {path}: HTTP {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as e:
            raise AppError(*ErrorCode.UPSTREAM_ERROR, http_status=502,
                           detail=f"{method} {path}: 非 JSON 响应 {e}")
        # CommonResult 解包（yudao 规范：code==0 成功）
        if isinstance(body, dict) and "code" in body:
            if body["code"] != 0:
                raise AppError(
                    "AI_2002", body.get("msg") or "点餐系统处理失败",
                    http_status=502, detail=f"{method} {path}: code={body['code']}",
                )
            return body.get("data")
        return body

    async def get(self, path: str, *, token: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, token=token, params=params)

    async def post(self, path: str, *, token: str, json_body: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, token=token, json_body=json_body)

    def get_sync(self, path: str, *, token: str, params: dict[str, Any] | None = None) -> Any:
        """同步版本（脚本/轮询线程用）。复用同一套签名与解包逻辑。"""
        url = f"{self._base_url}{path}"
        headers = self._headers(token, "GET", path)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as e:
            raise AppError(*ErrorCode.UPSTREAM_TIMEOUT, http_status=504, detail=f"GET {path}: {e}")
        except httpx.HTTPError as e:
            raise AppError(*ErrorCode.UPSTREAM_ERROR, http_status=502, detail=f"GET {path}: {e}")
        if resp.status_code >= 500 or resp.status_code == 401:
            raise AppError(*ErrorCode.UPSTREAM_ERROR, http_status=502,
                           detail=f"GET {path}: HTTP {resp.status_code}")
        body = resp.json()
        if isinstance(body, dict) and "code" in body and body["code"] != 0:
            raise AppError("AI_2002", body.get("msg") or "点餐系统处理失败",
                           http_status=502, detail=f"GET {path}: code={body['code']}")
        return body.get("data") if isinstance(body, dict) else body


@lru_cache
def get_java_client() -> JavaClient:
    return JavaClient()
