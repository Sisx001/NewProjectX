from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin


class Customer(IdMixin, TimestampMixin, Base):
    """A buyer known to the store. Guests get a record keyed by email/phone; registered users link via user_id."""

    __tablename__ = "customers"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    email: Mapped[str | None] = mapped_column(String(254), index=True)
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    tags: Mapped[list[Any]] = mapped_column(default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    marketing_consent: Mapped[bool] = mapped_column(default=False)
    consent_updated_at: Mapped[dt.datetime | None] = mapped_column()
    orders_count: Mapped[int] = mapped_column(default=0)
    total_spent_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    last_order_at: Mapped[dt.datetime | None] = mapped_column()
    locale: Mapped[str] = mapped_column(String(10), default="en")
    is_test: Mapped[bool] = mapped_column(default=False)
    legacy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    erased_at: Mapped[dt.datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("store_id", "user_id"),)


class Address(IdMixin, TimestampMixin, Base):
    """Bangladesh-first address. division/district/upazila use the canonical names from the locations dataset."""

    __tablename__ = "addresses"
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(40), default="Home")
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(32))
    line1: Mapped[str] = mapped_column(String(300))
    line2: Mapped[str] = mapped_column(String(300), default="")
    area: Mapped[str] = mapped_column(String(120), default="")
    upazila: Mapped[str] = mapped_column(String(120), default="")
    district: Mapped[str] = mapped_column(String(120))
    division: Mapped[str] = mapped_column(String(120), default="")
    postal_code: Mapped[str] = mapped_column(String(12), default="")
    is_default: Mapped[bool] = mapped_column(default=False)
