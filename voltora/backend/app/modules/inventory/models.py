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


class Supplier(IdMixin, TimestampMixin, Base):
    __tablename__ = "suppliers"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    contact_name: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str] = mapped_column(Text, default="")
    lead_time_days: Mapped[int] = mapped_column(default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    __table_args__ = (UniqueConstraint("store_id", "name"),)


class PurchaseOrder(IdMixin, TimestampMixin, Base):
    """draft -> ordered -> partially_received -> received, or cancelled (only before any receipt)."""

    __tablename__ = "purchase_orders"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    number: Mapped[str] = mapped_column(String(20))
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    reference: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    expected_at: Mapped[dt.date | None] = mapped_column()
    ordered_at: Mapped[dt.datetime | None] = mapped_column()
    received_at: Mapped[dt.datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("store_id", "number"),)


class PurchaseOrderLine(IdMixin, Base):
    __tablename__ = "purchase_order_lines"
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"),
                                                         index=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id"))
    quantity: Mapped[int] = mapped_column()
    received_quantity: Mapped[int] = mapped_column(default=0)
    unit_cost_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        CheckConstraint("quantity > 0", name="qty_pos"),
        CheckConstraint("received_quantity >= 0 AND received_quantity <= quantity", name="received_range"),
    )
