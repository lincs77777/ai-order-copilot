"""FastAPI 入口：lifespan 装配（设计文档 §3）。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.health import router as health_router
from app.api.v1.order import router as order_router
from app.api.v1.session import router as session_router
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler
from app.core.logging import get_request_id, set_request_id, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # RAG 菜单同步（轮询 diff，失败不影响启动）
    s = get_settings()
    if s.DEV_MOCK_UPSTREAM_TOKEN:
        from app.rag import sync_listener
        sync_listener.start(shop_id=2, token=s.DEV_MOCK_UPSTREAM_TOKEN)
    yield
    try:
        from app.rag import sync_listener
        sync_listener.stop()
    except Exception:
        pass


app = FastAPI(
    title="AI Order Copilot",
    version="0.1.0",
    description="yshop-drink 旁路 AI 点餐助手",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """X-Request-Id 贯穿网关→Agent→Java（设计文档 §9）。"""
    rid = set_request_id(request.headers.get("X-Request-Id"))
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


app.add_exception_handler(AppError, app_error_handler)

# CORS：白名单（禁 *）；dev 额外放行本机任意端口的 H5 调试来源
_origins = [o.strip() for o in get_settings().ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+" if get_settings().APP_ENV == "dev" else None,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
    expose_headers=["X-Request-Id"],
)

app.include_router(health_router)  # 内部已带 prefix=/api/v1
app.include_router(auth_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(order_router, prefix="/api/v1")
app.include_router(session_router, prefix="/api/v1")


@app.get("/metrics", include_in_schema=False)
def metrics():
    """Prometheus 抓取端点。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
