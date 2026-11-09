from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="customer")  # staff|customer
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|disabled|invited
    password_hash: Mapped[str | None] = mapped_column(String(255))
    email_verified_at: Mapped[dt.datetime | None] = mapped_column()
    mfa_secret_sealed: Mapped[str | None] = mapped_column(Text)
    mfa_pending_sealed: Mapped[str | None] = mapped_column(Text)
    mfa_enabled_at: Mapped[dt.datetime | None] = mapped_column()
    failed_logins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column()
    last_login_at: Mapped[dt.datetime | None] = mapped_column()
    locale: Mapped[str] = mapped_column(String(10), default="en")
    marketing_consent: Mapped[bool] = mapped_column(default=False)
    legacy_id: Mapped[str | None] = mapped_column(String(64), index=True)


class Role(IdMixin, TimestampMixin, Base):
    __tablename__ = "roles"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(300), default="")
    permissions: Mapped[list[Any]] = mapped_column(default=list)
    builtin: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (UniqueConstraint("store_id", "key"),)


class Membership(IdMixin, TimestampMixin, Base):
    """Staff access grant to a store. expires_at enables time-limited grants."""

    __tablename__ = "memberships"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    expires_at: Mapped[dt.datetime | None] = mapped_column()
    granted_by: Mapped[uuid.UUID | None] = mapped_column()
    __table_args__ = (UniqueConstraint("user_id", "store_id"),)


class AuthSession(IdMixin, Base):
    __tablename__ = "auth_sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    mfa_verified: Mapped[bool] = mapped_column(default=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column()
    revoked_at: Mapped[dt.datetime | None] = mapped_column()


class RecoveryCode(IdMixin, Base):
    __tablename__ = "mfa_recovery_codes"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[dt.datetime | None] = mapped_column()


class OneTimeToken(IdMixin, Base):
    """Password reset, email verification and staff invitations (only hashes stored)."""

    __tablename__ = "one_time_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[dt.datetime] = mapped_column()
    used_at: Mapped[dt.datetime | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ApiKey(IdMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    permissions: Mapped[list[Any]] = mapped_column(default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    expires_at: Mapped[dt.datetime | None] = mapped_column()
    last_used_at: Mapped[dt.datetime | None] = mapped_column()
    revoked_at: Mapped[dt.datetime | None] = mapped_column()
