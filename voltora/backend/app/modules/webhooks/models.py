from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class WebhookEndpoint(IdMixin, TimestampMixin, Base):
    """Outbound event subscription. Developer endpoints have a fixed URL; extension-owned endpoints may resolve the
    destination from the extension's settings/secrets at delivery time (e.g. a Slack incoming-webhook URL)."""

    __tablename__ = "webhook_endpoints"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str | None] = mapped_column(String(800))
    events: Mapped[list[Any]] = mapped_column(default=list)  # exact types, "prefix.*" or "*"
    format: Mapped[str] = mapped_column(String(20), default="voltora")  # voltora|slack|telegram
    target: Mapped[dict[str, Any]] = mapped_column(default=dict)
    secret_ref: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(default=True)
    owner_kind: Mapped[str] = mapped_column(String(20), default="developer")  # developer|plugin
    plugin_id: Mapped[str | None] = mapped_column(String(100), index=True)
    allowed_hosts: Mapped[list[Any] | None] = mapped_column()
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    last_delivery_at: Mapped[dt.datetime | None] = mapped_column()
    last_status: Mapped[str | None] = mapped_column(String(20))


class WebhookDelivery(IdMixin, Base):
    __tablename__ = "webhook_deliveries"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column()  # outbox event; null for pings/replays/automation calls
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # pending|succeeded|failed (will retry)|held (network disabled)|dead
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    response_status: Mapped[int | None] = mapped_column()
    response_body: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column()
    replay_of: Mapped[uuid.UUID | None] = mapped_column()
    source: Mapped[str] = mapped_column(String(40), default="event")  # event|ping|replay|automation
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    delivered_at: Mapped[dt.datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("endpoint_id", "event_id"),
                      Index("ix_webhook_deliveries_due", "status", "next_attempt_at"))
