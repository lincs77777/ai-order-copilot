"""全局配置：Pydantic Settings，环境变量注入，禁止硬编码 Key（设计文档 §11）。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ===== 基础 =====
    APP_ENV: str = "dev"
    APP_PORT: int = 8000

    # ===== Java 业务系统 =====
    JAVA_BASE_URL: str = "http://127.0.0.1:48081"
    JAVA_INTERNAL_SECRET: str = "change-me-in-prod"
    JAVA_TIMEOUT_SECONDS: float = 10.0

    # ===== 鉴权 =====
    AUTH_MODE: str = "redis"  # redis=方案A直读yshop Redis；introspection=方案B（待 Java 新增接口）
    AUTH_CACHE_TTL_SECONDS: int = 30

    # ===== LLM（DeepSeek 直连，OpenAI 兼容）=====
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_API_KEY: str = ""
    LLM_MODEL_MAIN: str = "deepseek-chat"
    LLM_MODEL_LIGHT: str = "deepseek-chat"
    LLM_TIMEOUT_SECONDS: float = 60.0
    FC_MAX_ROUNDS: int = 6

    # ===== Redis =====
    REDIS_URL: str = "redis://127.0.0.1:6379/2"
    # 方案A 鉴权直读 yshop 的 Redis（token 存默认 db 0）
    YSHOP_REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # ===== MySQL（ai-service 自有库）=====
    MYSQL_URL: str = "mysql+pymysql://root:change-me@127.0.0.1:3306/ai_copilot?charset=utf8mb4"

    # ===== 向量库 =====
    VECTOR_DB_BACKEND: str = "chroma"  # chroma（dev 内嵌）/ milvus（生产）
    VECTOR_DB_URL: str = "http://127.0.0.1:19530"
    CHROMA_PERSIST_DIR: str = "./data/chroma"

    # ===== Embedding（API 方案）=====
    EMBEDDING_BASE_URL: str = "https://api.siliconflow.cn/v1"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "BAAI/bge-m3"

    # ===== 限流与配额 =====
    RATE_LIMIT_USER_PER_MIN: int = 10
    RATE_LIMIT_IP_PER_MIN: int = 30
    RATE_LIMIT_USER_PER_DAY: int = 200
    TOKEN_QUOTA_PER_MEMBER_PER_DAY: int = 50000

    # ===== 订单确认态 =====
    ORDER_PREVIEW_TTL_SECONDS: int = 120

    # ===== 审计 =====
    AUDIT_ENCRYPT: bool = False

    # ===== dev 联调 =====
    # 仅 APP_ENV=dev 生效：工具层透传给 yshop 的固定 mock token
    # （yshop application-local.yaml 开了 mock-enable: true，token 格式 test{userId}）
    DEV_MOCK_UPSTREAM_TOKEN: str = ""

    # ===== CORS（设计 §5.1：白名单仅放行用户端 H5 域名，禁止 *）=====
    # 逗号分隔的精确来源；dev 额外放行 localhost/127.0.0.1 任意端口
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080"


@lru_cache
def get_settings() -> Settings:
    return Settings()
