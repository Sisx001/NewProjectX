"""Bookkeeping for the legacy (MongoDB) importer: id map for idempotent re-runs and a run history."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, utcnow


class LegacyIdMap(Base):
    """legacy string id -> new primary key. Presence of a row is what makes the importer insert-only and idempotent."""

    __tablename__ = "legacy_id_map"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    collection: Mapped[str] = mapped_column(String(60), primary_key=True)
    legacy_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    target_table: Mapped[str] = mapped_column(String(60))
    target_id: Mapped[uuid.UUID | None] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(20), default="created")  # created|linked
    imported_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class LegacyImportRun(IdMixin, Base):
    __tablename__ = "legacy_import_runs"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    mode: Mapped[str] = mapped_column(String(12))  # dry_run|apply
    source: Mapped[str] = mapped_column(String(400))
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
