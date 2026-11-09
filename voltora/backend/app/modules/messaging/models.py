from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class MessageTemplate(IdMixin, TimestampMixin, Base):
    """Owner-editable override of a built-in template (see service.DEFAULT_TEMPLATES)."""

    __tablename__ = "message_templates"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    locale: Mapped[str] = mapped_column(String(10), default="en")
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    __table_args__ = (UniqueConstraint("store_id", "key", "locale"),)


class OutboundMessage(IdMixin, Base):
    __tablename__ = "outbound_messages"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # email|sms|whatsapp|telegram
    to: Mapped[str] = mapped_column(String(254), index=True)
    template: Mapped[str | None] = mapped_column(String(80))
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    sensitive: Mapped[bool] = mapped_column(default=False)
    # queued|sending|sent|failed|held (held = no transport configured; never reported as sent)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    transport: Mapped[str | None] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    related: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column()
