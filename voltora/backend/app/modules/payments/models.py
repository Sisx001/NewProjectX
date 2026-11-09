"""Payment intents, provider webhook log, refunds, the immutable money ledger and per-store method config."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DDL, BigInteger, CheckConstraint, ForeignKey, Identity, Index, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class PaymentMethodConfig(IdMixin, TimestampMixin, Base):
    """Merchant configuration of one provider/method. Catalog metadata lives in code (providers/catalog.py)."""

    __tablename__ = "payment_methods"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(default=False)
    label: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    fee_minor: Mapped[int] = mapped_column(BigInteger, default=0)  # handling fee added to the order
    min_order_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    max_order_minor: Mapped[int | None] = mapped_column(BigInteger)
    position: Mapped[int] = mapped_column(default=0)
    mode: Mapped[str] = mapped_column(String(10), default="sandbox")  # sandbox|live
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)  # non-secret settings (bank instructions …)
    credential_fields: Mapped[list[Any]] = mapped_column(default=list)  # names of saved secret fields
    verification: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {state, checked_at, message}
    __table_args__ = (UniqueConstraint("store_id", "key"),)


class PaymentIntent(IdMixin, TimestampMixin, Base):
    """One attempt to collect money for an order, bound to amount + currency + mode."""

    __tablename__ = "payment_intents"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # cod|redirect|manual
    mode: Mapped[str] = mapped_column(String(10))  # sandbox|live
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    # requires_action|processing|awaiting_verification|succeeded|collected|failed|cancelled|expired
    status: Mapped[str] = mapped_column(String(30), index=True)
    captured_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    provider_ref: Mapped[str | None] = mapped_column(String(200), index=True)  # session / paymentID
    provider_txn_id: Mapped[str | None] = mapped_column(String(200))  # bKash trxID, bank reference …
    redirect_url: Mapped[str | None] = mapped_column(String(800))
    customer_reference: Mapped[str | None] = mapped_column(String(120))  # bank transfer ref typed by customer
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    simulate: Mapped[str | None] = mapped_column(String(20))  # sandbox gateway outcome under test
    remitted_at: Mapped[dt.datetime | None] = mapped_column()  # COD: courier paid the merchant
    remittance_ref: Mapped[str | None] = mapped_column(String(120))
    succeeded_at: Mapped[dt.datetime | None] = mapped_column()
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_pos"),
        CheckConstraint("refunded_minor <= captured_minor", name="refund_le_captured"),
        CheckConstraint("captured_minor <= amount_minor", name="captured_le_amount"),
    )


class PaymentEvent(IdMixin, Base):
    """Webhook / provider notification log. Dedupe is by (provider, provider_event_id) and only after success."""

    __tablename__ = "payment_events"
    store_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stores.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_event_id: Mapped[str | None] = mapped_column(String(200))
    intent_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(60), default="unknown")
    signature_valid: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="received")  # received|processed|rejected|failed
    reason: Mapped[str | None] = mapped_column(String(500))
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    headers: Mapped[dict[str, Any]] = mapped_column(default=dict)
    payload: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(default=1)
    received_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    processed_at: Mapped[dt.datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("provider", "provider_event_id"),)


class Refund(IdMixin, TimestampMixin, Base):
    __tablename__ = "refunds"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payment_intents.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    # awaiting_approval|processing|succeeded|failed|rejected
    status: Mapped[str] = mapped_column(String(20), index=True)
    method: Mapped[str] = mapped_column(String(40))  # provider key or manual payout method (bkash_send_money, bank, cash)
    reference: Mapped[str | None] = mapped_column(String(200))  # manual payout reference
    provider_refund_id: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(String(500), default="")
    lines: Mapped[list[Any]] = mapped_column(default=list)  # [{line_id, quantity, restock}]
    restocked: Mapped[bool] = mapped_column(default=False)
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    requested_by_label: Mapped[str | None] = mapped_column(String(200))
    approved_by: Mapped[uuid.UUID | None] = mapped_column()
    approved_by_label: Mapped[str | None] = mapped_column(String(200))
    completed_at: Mapped[dt.datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("store_id", "idempotency_key", name="uq_refunds_store_idem"),
                      CheckConstraint("amount_minor > 0", name="amount_pos"))


class LedgerEntry(Base):
    """Immutable signed money movements (positive = money in to the merchant). UPDATE/DELETE are blocked in SQL."""

    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    intent_id: Mapped[uuid.UUID | None] = mapped_column()
    refund_id: Mapped[uuid.UUID | None] = mapped_column()
    # payment_captured|cod_collected|refund|manual_payout|bank_transfer_received
    kind: Mapped[str] = mapped_column(String(40), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str] = mapped_column(String(40))
    reference: Mapped[str | None] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(10), default="live")
    memo: Mapped[str] = mapped_column(String(300), default="")
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    __table_args__ = (Index("ix_ledger_store_created", "store_id", "created_at"),)


event.listen(LedgerEntry.__table__, "after_create", DDL("""
CREATE OR REPLACE FUNCTION voltora_ledger_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'ledger_entries is append-only'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER ledger_entries_immutable BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH ROW EXECUTE FUNCTION voltora_ledger_immutable();
"""))


class SandboxWebhookDelivery(IdMixin, Base):
    """Signed notifications produced by the VOLTORA Sandbox Gateway / courier (simulation) awaiting delivery."""

    __tablename__ = "sandbox_webhook_deliveries"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    target: Mapped[str] = mapped_column(String(20))  # payments|shipping
    body: Mapped[str] = mapped_column(Text)
    headers: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|delivered|failed
    attempts: Mapped[int] = mapped_column(default=0)
    last_response: Mapped[str | None] = mapped_column(String(500))
    deliver_after: Mapped[dt.datetime] = mapped_column(default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
