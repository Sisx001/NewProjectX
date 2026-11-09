from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class PluginPackage(IdMixin, Base):
    """A validated manifest awaiting installation (from the built-in catalog or an upload). Provenance is kept."""

    __tablename__ = "plugin_packages"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    plugin_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[dict[str, Any]] = mapped_column()
    readme: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20))  # catalog|upload
    filename: Mapped[str | None] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class PluginInstallation(IdMixin, TimestampMixin, Base):
    __tablename__ = "plugin_installations"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    plugin_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[dict[str, Any]] = mapped_column()
    source: Mapped[str] = mapped_column(String(20))
    package_sha256: Mapped[str] = mapped_column(String(64))
    # disabled|enabled|broken|uninstalled (uninstalled = data kept for a later reinstall)
    status: Mapped[str] = mapped_column(String(20), default="disabled", index=True)
    granted_permissions: Mapped[list[Any]] = mapped_column(default=list)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    sandbox: Mapped[dict[str, Any]] = mapped_column(default=dict)  # last contract test
    health: Mapped[dict[str, Any]] = mapped_column(default=dict)
    history: Mapped[list[Any]] = mapped_column(default=list)  # previous versions for rollback
    installed_by: Mapped[uuid.UUID | None] = mapped_column()
    enabled_at: Mapped[dt.datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("store_id", "plugin_id"),)
