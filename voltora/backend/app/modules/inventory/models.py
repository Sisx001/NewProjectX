from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class Warehouse(IdMixin, TimestampMixin, Base):
    __tablename__ = "warehouses"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(20))
    address: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {line1, area, upazila, district, division}
    district: Mapped[str | None] = mapped_column(String(80))
    is_default: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    __table_args__ = (UniqueConstraint("store_id", "code"),)


class StockLevel(Base):
    """Server-authoritative stock per variant and warehouse. Change it only via inventory.ledger.apply_movement."""

    __tablename__ = "stock_levels"
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"), primary_key=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), primary_key=True)
    on_hand: Mapped[int] = mapped_column(default=0)
    reserved: Mapped[int] = mapped_column(default=0)
    reorder_point: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    __table_args__ = (
        CheckConstraint("reserved >= 0", name="reserved_nonneg"),
        CheckConstraint("on_hand >= 0", name="on_hand_nonneg"),
    )


class StockMovement(Base):
    """Immutable stock ledger. Balances are recorded after each movement for audit/reconciliation."""

    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    reason: Mapped[str] = mapped_column(String(30))
    # receive|adjust|count|reserve|release|ship|return|transfer_in|transfer_out|damage
    delta_on_hand: Mapped[int] = mapped_column(default=0)
    delta_reserved: Mapped[int] = mapped_column(default=0)
    balance_on_hand: Mapped[int] = mapped_column()
    balance_reserved: Mapped[int] = mapped_column()
    ref_type: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[str | None] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    __table_args__ = (Index("ix_stock_movements_ref", "ref_type", "ref_id"),)
