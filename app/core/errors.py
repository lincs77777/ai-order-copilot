"""统一错误码与异常体系（设计文档 §5.3）。

码段：
  AI_0xxx 通用      AI_1xxx 守卫拦截   AI_2xxx 工具执行
  AI_3xxx LLM 网关  AI_9xxx 降级
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ErrorCode:
    # 通用
    PARAM_INVALID = ("AI_0001", "参数非法")
    UNAUTHORIZED = ("AI_0002", "未授权或登录已过期")
    FORBIDDEN = ("AI_0003", "无权访问该资源")
    NOT_FOUND = ("AI_0004", "资源不存在")
    INTERNAL = ("AI_0500", "服务内部错误")
    # 守卫拦截
    PROMPT_INJECTION = ("AI_1001", "您的请求包含不被支持的内容")
    CONTENT_FILTERED = ("AI_1002", "回复内容已被安全策略过滤")
    # 工具执行
    UPSTREAM_TIMEOUT = ("AI_2001", "点餐系统响应超时，请稍后重试")
    UPSTREAM_ERROR = ("AI_2002", "点餐系统处理失败")
    PRODUCT_NOT_FOUND = ("AI_2003", "商品不存在或不属于当前门店")
    PRODUCT_OFF_SHELF = ("AI_2004", "商品已下架或售罄")
    SKU_NOT_FOUND = ("AI_2005", "商品规格不存在")
    STOCK_NOT_ENOUGH = ("AI_2006", "商品库存不足")
    PREVIEW_INVALID = ("AI_2007", "订单预览已失效，请重新预览")
    AMOUNT_CHANGED = ("AI_2008", "订单金额已变化，请重新预览")
    # LLM 网关
    LLM_TIMEOUT = ("AI_3001", "AI 服务响应超时")
    LLM_QUOTA_EXCEEDED = ("AI_3002", "AI 服务配额已耗尽")
    # 降级
    DEGRADED = ("AI_9001", "AI 服务暂时不可用，已为您切换传统点单")


class AppError(Exception):
    """业务异常基类。user_message 为用户可读文案；detail 仅供日志/trace。"""

    def __init__(
        self,
        code: str,
        user_message: str,
        http_status: int = 400,
        detail: Any = None,
        degraded: bool = False,
    ):
        self.code = code
        self.user_message = user_message
        self.http_status = http_status
        self.detail = detail
        self.degraded = degraded
        super().__init__(f"[{code}] {user_message}")

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.http_status,
            content={
                "code": self.code,
                "message": self.user_message,
                "degraded": self.degraded,
            },
        )


def err(
    code_pair: tuple[str, str],
    http_status: int = 400,
    detail: Any = None,
    degraded: bool = False,
) -> AppError:
    """快捷构造：err(ErrorCode.UNAUTHORIZED, 401)"""
    code, message = code_pair
    return AppError(code, message, http_status, detail, degraded)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return exc.to_response()
