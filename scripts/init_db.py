"""建库建表脚本（幂等）：python -m scripts.init_db

按设计文档 §6 建 6 张表：ai_session / ai_message / ai_cart_item /
ai_order_preview / ai_tool_audit / ai_feedback（InnoDB + utf8mb4）。
"""
from __future__ import annotations

import pymysql
from sqlalchemy import inspect, text

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.models import audit, cart, session  # noqa: F401  注册所有模型
from app.models.db import Base, engine


def ensure_database() -> None:
    """库不存在则创建（CREATE DATABASE 需在无库名连接上执行）。"""
    url = get_settings().MYSQL_URL
    parsed = pymysql.connect(
        host=url.split("@")[1].split(":")[0],
        port=int(url.split("@")[1].split(":")[1].split("/")[0]),
        user=url.split("//")[1].split(":")[0],
        password=url.split("//")[1].split(":")[1].split("@")[0],
        charset="utf8mb4",
    )
    try:
        with parsed.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS ai_copilot "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
        parsed.commit()
    finally:
        parsed.close()


def main() -> None:
    setup_logging()
    ensure_database()
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())
    print("tables in ai_copilot:")
    for t in tables:
        with engine.connect() as conn:
            ddl_engine = conn.execute(
                text(f"SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
                     f"WHERE TABLE_SCHEMA='ai_copilot' AND TABLE_NAME='{t}'")
            ).fetchone()
        print(f"  - {t}  engine={ddl_engine[0]} collation={ddl_engine[1]}")
    assert len(tables) >= 6, f"expect >=6 tables, got {len(tables)}"
    print("init_db: OK")


if __name__ == "__main__":
    main()
