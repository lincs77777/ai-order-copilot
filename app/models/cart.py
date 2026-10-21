"""会话购物车 ORM（设计文档 §6：ai_cart_item；yshop 无购物车服务，真源在 ai-service）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base


class AiCartItem(Base):
    __tablename__ = "ai_cart_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # yshop_store_product.id
    sku_unique: Mapped[str] = mapped_column(String(64), nullable=False)  # SKU 唯一码（attr_value.unique）
    product_name: Mapped[str] = mapped_column(String(128), nullable=False)  # 冗余展示用
    spec_text: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 规格描述（展示用）
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # 1–20
    price_cached: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)  # 加购时校验到的单价（展示参考）
    verified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active/removed
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    __table_args__ = ({"comment": "会话购物车表"},)
