"""Carts, orders, order lines and the order timeline. Money is integer minor units."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Sequence, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow

ORDER_NUMBER_SEQ = Sequence("order_number_seq", start=100001)


class Cart(IdMixin, TimestampMixin, Base):
    __tablename__ = "carts"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)  # guest cookie vt_cart (hashed)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")
    discount_code: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active|merged|converted
    order_id: Mapped[uuid.UUID | None] = mapped_column()


class CartLine(IdMixin, Base):
    __tablename__ = "cart_lines"
    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"))
    quantity: Mapped[int] = mapped_column()
    added_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    __table_args__ = (UniqueConstraint("cart_id", "variant_id"), CheckConstraint("quantity > 0", name="qty_pos"))


class Order(IdMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    seq: Mapped[int] = mapped_column(BigInteger, ORDER_NUMBER_SEQ, server_default=ORDER_NUMBER_SEQ.next_value(),
                                     unique=True)
    number: Mapped[str] = mapped_column(String(24), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    access_token_hash: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    email: Mapped[str | None] = mapped_column(String(254), index=True)
    phone: Mapped[str] = mapped_column(String(16), index=True)  # normalized 01XXXXXXXXX
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), index=True)
    payment_status: Mapped[str] = mapped_column(String(30), default="unpaid", index=True)
    fulfillment_status: Mapped[str] = mapped_column(String(30), default="unfulfilled", index=True)
    payment_method: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3))
    subtotal_minor: Mapped[int] = mapped_column(BigInteger)
    discount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    shipping_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    payment_fee_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_inclusive: Mapped[bool] = mapped_column(default=True)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    paid_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    discount_code: Mapped[str | None] = mapped_column(String(40))
    shipping_rate_id: Mapped[uuid.UUID | None] = mapped_column()
    shipping_label: Mapped[str] = mapped_column(String(160), default="")
    shipping_address: Mapped[dict[str, Any]] = mapped_column(default=dict)
    billing_address: Mapped[dict[str, Any]] = mapped_column(default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[Any]] = mapped_column(default=list)
    hold_reason: Mapped[str | None] = mapped_column(String(300))
    cancel_reason: Mapped[str | None] = mapped_column(String(300))
    placed_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column()
    cancelled_at: Mapped[dt.datetime | None] = mapped_column()
    delivered_at: Mapped[dt.datetime | None] = mapped_column()
    expires_at: Mapped[dt.datetime | None] = mapped_column(index=True)  # unpaid online orders
    is_test: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (
        UniqueConstraint("store_id", "idempotency_key", name="uq_orders_store_idem"),
        CheckConstraint("total_minor >= 0", name="total_nonneg"),
        CheckConstraint("refunded_minor <= paid_minor", name="refund_le_paid"),
        Index("ix_orders_store_placed", "store_id", "placed_at"),
    )


class OrderLine(IdMixin, Base):
    __tablename__ = "order_lines"
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("variants.id", ondelete="SET NULL"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    sku: Mapped[str] = mapped_column(String(80), default="")
    title: Mapped[str] = mapped_column(String(300))
    variant_title: Mapped[str] = mapped_column(String(200), default="")
    image_url: Mapped[str | None] = mapped_column(String(800))
    quantity: Mapped[int] = mapped_column()
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    discount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    weight_grams: Mapped[int] = mapped_column(default=0)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"))  # reservation location
    reserved_qty: Mapped[int] = mapped_column(default=0)  # currently held reservation
    fulfilled_qty: Mapped[int] = mapped_column(default=0)
    refunded_qty: Mapped[int] = mapped_column(default=0)
    returned_qty: Mapped[int] = mapped_column(default=0)
    __table_args__ = (
        CheckConstraint("quantity > 0", name="qty_pos"),
        CheckConstraint("fulfilled_qty <= quantity", name="fulfilled_le_qty"),
        CheckConstraint("returned_qty <= fulfilled_qty", name="returned_le_fulfilled"),
        CheckConstraint("refunded_qty <= quantity", name="refunded_le_qty"),
    )


class OrderEvent(IdMixin, Base):
    """Order timeline. `public` events are shown to the customer."""

    __tablename__ = "order_events"
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # placed|status|payment|shipment|refund|note|tag|discount|email
    message: Mapped[str] = mapped_column(Text)
    public: Mapped[bool] = mapped_column(default=False)
    actor_kind: Mapped[str] = mapped_column(String(20), default="system")
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    actor_label: Mapped[str | None] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
