"""Shipping zones, rates, courier accounts and shipments."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class ShippingZone(IdMixin, TimestampMixin, Base):
    """A set of regions. Each region is {division?, district?, upazila?}; empty regions = everywhere (fallback).

    The most specific matching region wins (upazila > district > division > fallback)."""

    __tablename__ = "shipping_zones"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    regions: Mapped[list[Any]] = mapped_column(default=list)
    position: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True)
    seed_key: Mapped[str | None] = mapped_column(String(40))  # set for seeded defaults (editable)


class ShippingRate(IdMixin, TimestampMixin, Base):
    __tablename__ = "shipping_rates"
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipping_zones.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10), default="flat")  # flat|weight
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)  # flat fee, or base fee for weight rates
    base_weight_grams: Mapped[int] = mapped_column(default=1000)  # weight included in amount_minor
    per_kg_minor: Mapped[int] = mapped_column(BigInteger, default=0)  # each additional (started) kg
    free_over_minor: Mapped[int | None] = mapped_column(BigInteger)  # free when merchandise total ≥ this
    min_order_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    cod_allowed: Mapped[bool] = mapped_column(default=True)
    cod_fee_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    eta_label: Mapped[str] = mapped_column(String(80), default="")
    courier_key: Mapped[str | None] = mapped_column(String(40))  # preferred courier when booking
    active: Mapped[bool] = mapped_column(default=True)
    position: Mapped[int] = mapped_column(default=0)
    is_seed_default: Mapped[bool] = mapped_column(default=False)


class CourierAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "courier_accounts"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    courier_key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(120))
    mode: Mapped[str] = mapped_column(String(10), default="sandbox")
    enabled: Mapped[bool] = mapped_column(default=False)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)  # non-secret (store id, pickup ids …)
    credential_fields: Mapped[list[Any]] = mapped_column(default=list)
    verification: Mapped[dict[str, Any]] = mapped_column(default=dict)
    __table_args__ = (UniqueConstraint("store_id", "courier_key", "mode"),)


class Shipment(IdMixin, TimestampMixin, Base):
    __tablename__ = "shipments"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("courier_accounts.id"))
    courier_key: Mapped[str] = mapped_column(String(40), index=True)
    tracking_code: Mapped[str | None] = mapped_column(String(120), index=True)
    tracking_url: Mapped[str | None] = mapped_column(String(500))
    # booked|picked_up|in_transit|out_for_delivery|delivered|failed_attempt|returning|returned|cancelled
    status: Mapped[str] = mapped_column(String(30), default="booked", index=True)
    lines: Mapped[list[Any]] = mapped_column(default=list)  # [{line_id, quantity}]
    weight_grams: Mapped[int] = mapped_column(default=0)
    cod_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    is_simulation: Mapped[bool] = mapped_column(default=False)
    provider_data: Mapped[dict[str, Any]] = mapped_column(default=dict)  # booking overrides + provider refs
    restocked: Mapped[bool] = mapped_column(default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    delivered_at: Mapped[dt.datetime | None] = mapped_column()


class ShipmentEvent(IdMixin, Base):
    __tablename__ = "shipment_events"
    shipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"), index=True)
    courier_key: Mapped[str] = mapped_column(String(40))
    provider_event_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    provider_status: Mapped[str | None] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="webhook")  # webhook|poll|staff|simulation
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)
    at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    __table_args__ = (UniqueConstraint("courier_key", "provider_event_id"),)
