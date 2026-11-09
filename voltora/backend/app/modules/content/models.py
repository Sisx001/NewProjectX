from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow

PAGE_KINDS = ("page", "global", "template", "symbol")


class ContentPage(IdMixin, TimestampMixin, Base):
    """A versioned document: storefront page, global section (header/footer/announcement), template or symbol.

    The working draft lives on the row (`draft_document`, guarded by `draft_version` for optimistic concurrency);
    every publish, rollback, import and checkpoint is an immutable `PageRevision`.
    """

    __tablename__ = "content_pages"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="page")
    slug: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(200))
    seo: Mapped[dict[str, Any]] = mapped_column(default=dict)
    draft_document: Mapped[dict[str, Any]] = mapped_column(default=dict)
    draft_version: Mapped[int] = mapped_column(default=1)
    draft_updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    draft_updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    published_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_page_revisions.id", ondelete="SET NULL", use_alter=True))
    published_at: Mapped[dt.datetime | None] = mapped_column()
    needs_review: Mapped[bool] = mapped_column(default=False)  # seeded templates the owner must review
    archived_at: Mapped[dt.datetime | None] = mapped_column()
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    __table_args__ = (UniqueConstraint("store_id", "kind", "slug"),)


class PageRevision(IdMixin, Base):
    __tablename__ = "content_page_revisions"
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_pages.id", ondelete="CASCADE"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    number: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(20))  # publish|rollback|checkpoint|import|seed
    document: Mapped[dict[str, Any]] = mapped_column()
    title: Mapped[str] = mapped_column(String(200))
    seo: Mapped[dict[str, Any]] = mapped_column(default=dict)
    message: Mapped[str] = mapped_column(String(500), default="")
    source_number: Mapped[int | None] = mapped_column()
    diff: Mapped[dict[str, Any]] = mapped_column(default=dict)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    author_label: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    __table_args__ = (UniqueConstraint("page_id", "number"),)


class Theme(IdMixin, TimestampMixin, Base):
    __tablename__ = "content_themes"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(80))
    family: Mapped[str] = mapped_column(String(60), default="")
    draft_manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    draft_version: Mapped[int] = mapped_column(default=1)
    draft_updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    published_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_theme_revisions.id", ondelete="SET NULL", use_alter=True))
    is_live: Mapped[bool] = mapped_column(default=False)
    forked_from: Mapped[str | None] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(20), default="starter")  # starter|fork|import|plugin
    __table_args__ = (UniqueConstraint("store_id", "key"),
                      Index("uq_content_themes_live", "store_id", unique=True,
                            postgresql_where=text("is_live")))


class ThemeRevision(IdMixin, Base):
    __tablename__ = "content_theme_revisions"
    theme_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_themes.id", ondelete="CASCADE"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    number: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(20))
    manifest: Mapped[dict[str, Any]] = mapped_column()
    message: Mapped[str] = mapped_column(String(500), default="")
    source_number: Mapped[int | None] = mapped_column()
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    author_label: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    __table_args__ = (UniqueConstraint("theme_id", "number"),)
