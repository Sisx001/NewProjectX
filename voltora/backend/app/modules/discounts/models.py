from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class Discount(IdMixin, TimestampMixin, Base):
    __tablename__ = "discounts"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))  # stored upper-case
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20))  # percentage|fixed|free_shipping
    percent: Mapped[int | None] = mapped_column()  # 1..100 for percentage
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)  # fixed
    max_discount_minor: Mapped[int | None] = mapped_column(BigInteger)  # cap for percentage
    min_subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    applies_to: Mapped[str] = mapped_column(String(20), default="all")  # all|products|categories
    product_ids: Mapped[list[Any]] = mapped_column(default=list)
    category_ids: Mapped[list[Any]] = mapped_column(default=list)
    starts_at: Mapped[dt.datetime | None] = mapped_column()
    ends_at: Mapped[dt.datetime | None] = mapped_column()
    usage_limit: Mapped[int | None] = mapped_column()
    per_customer_limit: Mapped[int | None] = mapped_column()
    used_count: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("store_id", "code"),
        CheckConstraint("used_count >= 0", name="used_nonneg"),
        CheckConstraint("usage_limit IS NULL OR used_count <= usage_limit", name="used_le_limit"),
    )


class DiscountRedemption(IdMixin, Base):
    __tablename__ = "discount_redemptions"
    discount_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("discounts.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    customer_key: Mapped[str] = mapped_column(String(300), index=True)  # user:<id> | phone:<01…> | email:<…>
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|released
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    released_at: Mapped[dt.datetime | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(Text)
