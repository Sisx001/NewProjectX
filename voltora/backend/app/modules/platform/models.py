"""Platform-wide tables: stores, audit trail, canonical settings, vault, outbox, idempotency, notifications, install."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger, ForeignKey, Identity, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class Store(IdMixin, TimestampMixin, Base):
    __tablename__ = "stores"
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")
    locale: Mapped[str] = mapped_column(String(10), default="en")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Dhaka")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|maintenance|closed
    environment: Mapped[str] = mapped_column(String(20), default="development")
    legacy_id: Mapped[str | None] = mapped_column(String(64))


class AuditEvent(Base):
    """Append-only, hash-chained per store (tamper evidence, not tamper proof)."""

    __tablename__ = "audit_events"
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    store_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stores.id"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor_kind: Mapped[str] = mapped_column(String(20))  # user|system|ai|plugin|api_key
    actor_label: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(80))
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)

    __table_args__ = (Index("ix_audit_target", "target_type", "target_id"),)


class SettingValue(TimestampMixin, Base):
    __tablename__ = "setting_values"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column()


class ConfigRevision(IdMixin, Base):
    __tablename__ = "config_revisions"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    changes: Mapped[dict[str, Any]] = mapped_column()  # {key: {"from":..,"to":..}}
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)


class SecretRecord(IdMixin, TimestampMixin, Base):
    """Sealed credential. Plaintext never leaves the server process; APIs return masked hints only."""

    __tablename__ = "secrets"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    ref: Mapped[str] = mapped_column(String(160))  # e.g. payments.sslcommerz.store_password
    sealed: Mapped[str] = mapped_column(Text)
    hint: Mapped[str | None] = mapped_column(String(16))
    owner_kind: Mapped[str] = mapped_column(String(30), default="core")  # core|plugin|ai
    owner_ref: Mapped[str | None] = mapped_column(String(120))
    rotated_at: Mapped[dt.datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("store_id", "ref"),)


class OutboxEvent(IdMixin, Base):
    """Domain events written in the same transaction as the state change, dispatched by the worker."""

    __tablename__ = "outbox_events"
    store_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stores.id"), index=True)
    type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|done|failed|dead
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    results: Mapped[dict[str, Any]] = mapped_column(default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    processed_at: Mapped[dt.datetime | None] = mapped_column()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any] | None] = mapped_column()
    status_code: Mapped[int | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Notification(IdMixin, Base):
    __tablename__ = "notifications"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # null = all staff with permission
    permission: Mapped[str | None] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(10), default="info")
    read_by: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)


class InstallState(Base):
    __tablename__ = "install_state"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    bootstrap_token_hash: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[dict[str, Any]] = mapped_column(default=dict)
    completed_at: Mapped[dt.datetime | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class LogRecord(Base):
    """Structured, redacted application log retained for the in-app log viewer."""

    __tablename__ = "log_records"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    level: Mapped[str] = mapped_column(String(10), index=True)
    logger: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(40), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)


class BackupRecord(IdMixin, Base):
    __tablename__ = "backups"
    kind: Mapped[str] = mapped_column(String(20), default="database")
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|succeeded|failed
    path: Mapped[str | None] = mapped_column(String(400))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    encrypted: Mapped[bool] = mapped_column(default=False)
    restore_verified_at: Mapped[dt.datetime | None] = mapped_column()
    restore_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column()
