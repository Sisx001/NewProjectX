from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin


class Category(IdMixin, TimestampMixin, Base):
    __tablename__ = "categories"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    seo: Mapped[dict[str, Any]] = mapped_column(default=dict)
    legacy_id: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("store_id", "slug"),)


class Brand(IdMixin, TimestampMixin, Base):
    __tablename__ = "brands"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(160))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (UniqueConstraint("store_id", "slug"),)


class Product(IdMixin, TimestampMixin, Base):
    __tablename__ = "products"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")  # sanitized on write
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft|active|archived
    product_type: Mapped[str] = mapped_column(String(40), default="physical")  # physical|digital|service
    brand_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"))
    tags: Mapped[list[Any]] = mapped_column(default=list)
    options: Mapped[list[Any]] = mapped_column(default=list)  # [{"name": "Size", "values": ["S","M"]}]
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)  # custom fields
    seo: Mapped[dict[str, Any]] = mapped_column(default=dict)
    tax_class: Mapped[str] = mapped_column(String(40), default="standard")
    is_demo: Mapped[bool] = mapped_column(default=False, index=True)
    published_at: Mapped[dt.datetime | None] = mapped_column()
    legacy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    __table_args__ = (UniqueConstraint("store_id", "slug"),)


class ProductCategory(Base):
    __tablename__ = "product_categories"
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(default=0)


class ProductMedia(IdMixin, Base):
    __tablename__ = "product_media"
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(800))
    alt: Mapped[str] = mapped_column(String(300), default="")
    position: Mapped[int] = mapped_column(default=0)
    variant_ids: Mapped[list[Any]] = mapped_column(default=list)


class Variant(IdMixin, TimestampMixin, Base):
    """Sellable unit. Prices are integer minor units in the store currency."""

    __tablename__ = "variants"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(80))
    barcode: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200), default="Default")
    option_values: Mapped[dict[str, Any]] = mapped_column(default=dict)
    price_minor: Mapped[int] = mapped_column(BigInteger)
    compare_at_minor: Mapped[int | None] = mapped_column(BigInteger)
    cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    weight_grams: Mapped[int] = mapped_column(default=0)
    track_inventory: Mapped[bool] = mapped_column(default=True)
    allow_backorder: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    legacy_id: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("store_id", "sku"),
        CheckConstraint("price_minor >= 0", name="price_nonneg"),
    )
