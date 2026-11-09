"""Payment intents, webhook reconciliation, refunds and the money ledger.

Invariants
* An order is marked paid only from a verified provider event (signed webhook / server-to-server execute or
  validation) or a staff verification of a manual transfer — never from a browser redirect.
* Webhook dedupe happens only after successful processing; failed processing leaves the event retryable.
* sum(pending + succeeded refunds) ≤ captured, enforced under a row lock on the payment intent.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.errors import AppError, Conflict, Forbidden, NotFound
from ...core.services import SYSTEM, Actor, audit, emit, event_type, get_setting, periodic, request_fingerprint, setting
from ...db import session_factory, utcnow
from ..commerce.models import Order, OrderLine
from ..commerce.orders import (
    EV_ORDER_PAID,
    EV_ORDER_REFUNDED,
    customer_email,
    lines_of,
    order_access_token,
    recompute_payment_status,
    timeline,
    transition,
)
from .models import LedgerEntry, PaymentEvent, PaymentIntent, PaymentMethodConfig, Refund, SandboxWebhookDelivery
from .providers import get_provider
from .providers.base import (
    SIGNATURE_HEADER,
    InvalidSignature,
    ProviderContext,
    ProviderError,
    ProviderEvent,
    ProviderUnavailable,
    ensure_secret,
    sign,
)
from .providers.builtin import SANDBOX_SECRET_REF
from .providers.catalog import BY_KEY, enableable, state_for

log = logging.getLogger("voltora.payments")

EV_PAYMENT_SUCCEEDED = event_type("payment.succeeded", "A verified payment was captured")
EV_PAYMENT_FAILED = event_type("payment.failed", "A payment attempt failed or was cancelled")
EV_REFUND_SUCCEEDED = event_type("refund.succeeded", "A refund completed")

REFUND_APPROVAL_THRESHOLD = setting(
    key="refunds.approval_threshold_minor", type="money", default=0, min=0, group="Payments",
    label="Refunds above this amount need a second approver",
    description="0 turns dual control off. Above the threshold a different staff member with refund permission "
                "must approve before money moves.", write_permission="payments.manage")

OPEN_INTENT = ("pending", "requires_action", "processing", "awaiting_verification")
MANUAL_PAYOUT_METHODS = {"cash": "Cash", "bkash_send_money": "bKash send money", "nagad_send_money": "Nagad send money",
                         "bank_transfer": "Bank transfer", "store_credit_manual": "Store credit (manual)",
                         "other": "Other"}


def payments_mode() -> str:
    return get_settings().payments_mode


def method_config(db: Session, store_id: uuid.UUID, key: str, create: bool = False) -> PaymentMethodConfig | None:
    row = db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.store_id == store_id,
                                                       PaymentMethodConfig.key == key)).scalar_one_or_none()
    if row is None and create:
        entry = catalog_entry(key)
        row = PaymentMethodConfig(store_id=store_id, key=key, enabled=False, label=entry["label"] if entry else key,
                                  mode=payments_mode(), settings={}, credential_fields=[], verification={})
        db.add(row)
        db.flush()
    return row


def catalog_entry(key: str) -> dict | None:
    if key in BY_KEY:
        return BY_KEY[key]
    p = get_provider(key)
    if p is None:
        return None
    return {"key": key, "label": p.label, "kind": p.kind, "region": "Plugin", "credential_fields": [],
            "base_state": getattr(p, "catalog_state", "adapter_draft_unverified"), "docs_url": None, "notes": ""}


def ctx_for(db: Session, store_id: uuid.UUID, key: str, mode: str | None = None) -> ProviderContext:
    cfg = method_config(db, store_id, key)
    return ProviderContext(db=db, store_id=store_id, key=key, mode=mode or (cfg.mode if cfg else payments_mode()),
                           config=cfg)


def add_ledger(db: Session, store_id: uuid.UUID, *, kind: str, amount_minor: int, currency: str, method: str,
               mode: str, order_id=None, intent_id=None, refund_id=None, reference: str | None = None, memo: str = "",
               actor: Actor = SYSTEM) -> None:
    db.add(LedgerEntry(store_id=store_id, kind=kind, amount_minor=amount_minor, currency=currency, method=method,
                       mode=mode, order_id=order_id, intent_id=intent_id, refund_id=refund_id, reference=reference,
                       memo=memo[:300], actor_id=actor.id))


# ---------------------------------------------------------------- checkout methods
def checkout_methods(db: Session, store, total_minor: int, *, cod_allowed: bool = True,
                     cod_fee_minor: int = 0) -> list[dict]:
    rows = db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.store_id == store.id,
                                                        PaymentMethodConfig.enabled.is_(True))
                      .order_by(PaymentMethodConfig.position, PaymentMethodConfig.key)).scalars()
    out = []
    mode = payments_mode()
    for cfg in rows:
        entry = catalog_entry(cfg.key)
        provider = get_provider(cfg.key)
        if entry is None or provider is None:
            continue
        ok, reason = enableable(entry, cfg, mode)
        if ok and cfg.min_order_minor and total_minor < cfg.min_order_minor:
            ok, reason = False, f"Available for orders of at least ৳{cfg.min_order_minor / 100:,.0f}"
        if ok and cfg.max_order_minor and total_minor > cfg.max_order_minor:
            ok, reason = False, f"Available for orders up to ৳{cfg.max_order_minor / 100:,.0f}"
        if ok and provider.kind == "cod" and not cod_allowed:
            ok, reason = False, "Cash on delivery isn't offered for this delivery option"
        fee = cfg.fee_minor + (cod_fee_minor if provider.kind == "cod" else 0)
        out.append({"key": cfg.key, "label": cfg.label or entry["label"], "kind": provider.kind,
                    "description": cfg.description or provider.description, "fee_minor": fee, "available": ok,
                    "unavailable_reason": reason, "sandbox": provider.simulation or cfg.mode == "sandbox"
                    and provider.kind == "redirect",
                    "instructions": (cfg.settings or {}).get("instructions") if provider.kind == "manual" else None})
    return out


# ---------------------------------------------------------------- intents
def start_payment(db: Session, store_id: uuid.UUID, order: Order, method_key: str) -> dict:
    """Create a payment intent for the order's outstanding amount and return the customer's next step."""
    provider = get_provider(method_key)
    if provider is None:
        raise AppError("payment_method_unavailable", "This payment method is not available")
    for old in db.execute(select(PaymentIntent).where(PaymentIntent.order_id == order.id,
                                                      PaymentIntent.status.in_(["requires_action", "processing"]))
                          .with_for_update()).scalars():
        old.status = "cancelled"
        old.failure_reason = "Superseded by a new attempt"
    ctx = ctx_for(db, store_id, method_key)
    amount = order.total_minor - order.paid_minor
    intent = PaymentIntent(id=uuid.uuid4(), store_id=store_id, order_id=order.id, provider=method_key,
                           kind=provider.kind, mode="sandbox" if provider.simulation else ctx.mode, amount_minor=amount,
                           currency=order.currency, status="pending" if provider.kind in ("cod", "manual")
                           else "requires_action")
    db.add(intent)
    db.flush()
    nxt = provider.create_intent(ctx, intent, order)
    return nxt


def latest_intent(db: Session, order_id: uuid.UUID) -> PaymentIntent | None:
    return db.execute(select(PaymentIntent).where(PaymentIntent.order_id == order_id)
                      .order_by(PaymentIntent.created_at.desc()).limit(1)).scalar_one_or_none()


def next_for(db: Session, order: Order) -> dict:
    it = latest_intent(db, order.id)
    if order.status == "awaiting_payment" and it and it.status == "requires_action" and it.redirect_url:
        return {"type": "redirect", "url": it.redirect_url}
    return {"type": "done"}


def _record_event(db: Session, store_id, provider: str, ev: ProviderEvent | None, headers: dict, raw: bytes, *,
                  signature_valid: bool, status: str, reason: str | None = None) -> PaymentEvent:
    safe_headers = {k: v for k, v in headers.items() if k.lower() in (SIGNATURE_HEADER, "content-type", "user-agent")}
    row = PaymentEvent(store_id=store_id, provider=provider, provider_event_id=ev.event_id if ev else None,
                       intent_id=ev.intent_id if ev else None, type=ev.type if ev else "unknown",
                       signature_valid=signature_valid, status=status, reason=reason,
                       amount_minor=ev.amount_minor if ev else None, currency=ev.currency if ev else None,
                       headers=safe_headers, payload=raw.decode("utf-8", "replace")[:8000])
    db.add(row)
    return row


def handle_webhook(db: Session, store, provider_key: str, headers: dict[str, str], raw: bytes) -> dict:
    """Verify, dedupe and apply one provider notification. Commits. Returns {status, http}."""
    provider = get_provider(provider_key)
    if provider is None:
        raise NotFound("Payment provider")
    ctx = ctx_for(db, store.id, provider_key)
    try:
        ev = provider.parse_webhook(ctx, headers, raw)
    except InvalidSignature as exc:
        db.rollback()
        _record_event(db, store.id, provider_key, None, headers, raw, signature_valid=False, status="rejected",
                      reason=str(exc))
        db.commit()
        log.warning("Rejected %s webhook: %s", provider_key, exc)
        return {"status": "rejected", "reason": str(exc), "http": 400}
    return process_event(db, store.id, provider_key, ev, headers=headers, raw=raw)


def process_event(db: Session, store_id: uuid.UUID, provider_key: str, ev: ProviderEvent, *, headers: dict | None = None,
                  raw: bytes = b"") -> dict:
    headers = headers or {}
    stmt = insert(PaymentEvent).values(
        id=uuid.uuid4(), store_id=store_id, provider=provider_key, provider_event_id=ev.event_id,
        intent_id=ev.intent_id, type=ev.type, signature_valid=True, status="received", amount_minor=ev.amount_minor,
        currency=ev.currency, headers={k: v for k, v in headers.items() if k.lower() in (SIGNATURE_HEADER,
                                                                                          "content-type")},
        payload=(raw.decode("utf-8", "replace") if raw else json.dumps(ev.raw, default=str))[:8000],
        attempts=0, received_at=utcnow()).on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
    db.execute(stmt)
    row = db.execute(select(PaymentEvent).where(PaymentEvent.provider == provider_key,
                                                PaymentEvent.provider_event_id == ev.event_id).with_for_update()
                     ).scalar_one()
    row.attempts += 1
    if row.status in ("processed", "rejected"):
        db.commit()
        return {"status": "duplicate", "original_status": row.status, "http": 200}
    try:
        outcome, reason = apply_event(db, store_id, provider_key, ev)
        row.status = "processed" if outcome == "processed" else "rejected"
        row.reason = reason
        row.processed_at = utcnow()
        db.commit()
        return {"status": row.status, "reason": reason, "http": 200 if outcome == "processed" else 422}
    except Exception as exc:
        db.rollback()
        db.execute(insert(PaymentEvent).values(
            id=uuid.uuid4(), store_id=store_id, provider=provider_key, provider_event_id=ev.event_id,
            intent_id=ev.intent_id, type=ev.type, signature_valid=True, status="failed", reason=str(exc)[:500],
            attempts=1, received_at=utcnow(), headers={}, payload="")
            .on_conflict_do_update(index_elements=["provider", "provider_event_id"],
                                   set_={"status": "failed", "reason": str(exc)[:500]}))
        db.commit()
        raise


def apply_event(db: Session, store_id: uuid.UUID, provider_key: str, ev: ProviderEvent) -> tuple[str, str | None]:
    intent = db.execute(select(PaymentIntent).where(PaymentIntent.id == ev.intent_id,
                                                    PaymentIntent.store_id == store_id).with_for_update()
                        ).scalar_one_or_none() if ev.intent_id else None
    if intent is None:
        return "rejected", "Unknown payment intent"
    if intent.provider != provider_key:
        return "rejected", "Event provider does not match the payment"
    if ev.livemode is not None and ev.livemode != (intent.mode == "live"):
        return "rejected", "Event mode (live/sandbox) does not match the payment"
    order = db.execute(select(Order).where(Order.id == intent.order_id).with_for_update()).scalar_one()
    if ev.type == "payment.succeeded":
        if ev.amount_minor != intent.amount_minor or (ev.currency or "").upper() != intent.currency:
            timeline(db, order, "payment", f"Rejected a {provider_key} notification: amount/currency mismatch "
                                           f"({ev.amount_minor} {ev.currency} vs {intent.amount_minor} {intent.currency})")
            return "rejected", "Amount or currency does not match the payment"
        if intent.status in ("succeeded", "collected"):
            return "processed", "Already captured"
        was = intent.status
        intent.status = "succeeded"
        intent.captured_minor = intent.amount_minor
        intent.provider_txn_id = ev.provider_txn_id or intent.provider_txn_id
        intent.provider_ref = ev.provider_ref or intent.provider_ref
        intent.succeeded_at = utcnow()
        intent.failure_reason = None
        add_ledger(db, store_id, kind="payment_captured", amount_minor=intent.amount_minor, currency=intent.currency,
                   method=provider_key, mode=intent.mode, order_id=order.id, intent_id=intent.id,
                   reference=intent.provider_txn_id, memo=f"Order {order.number}")
        order.paid_minor += intent.amount_minor
        recompute_payment_status(order)
        timeline(db, order, "payment", f"Payment of ৳{intent.amount_minor / 100:,.2f} confirmed by {provider_key}"
                 + (" (simulation)" if intent.mode == "sandbox" else ""), public=True,
                 data={"intent_id": str(intent.id), "txn": intent.provider_txn_id})
        if order.status == "awaiting_payment":
            order.expires_at = None
            transition(db, order, "confirmed", note="Payment verified")
        elif order.status == "on_hold" and order.held_from == "awaiting_payment":
            order.held_from = "confirmed"
        elif order.status == "cancelled" or was in ("cancelled", "expired"):
            timeline(db, order, "payment", "Payment arrived after the order was cancelled — refund required")
            from ..platform.api import notify

            notify(db, store_id, "payment.late", f"Payment received for cancelled order {order.number}",
                   "Refund the customer from the order page.", f"/admin/orders/{order.number}", "orders.refund",
                   "warning")
        emit(db, store_id, EV_PAYMENT_SUCCEEDED, {"order_id": str(order.id), "number": order.number,
                                                  "intent_id": str(intent.id), "amount_minor": intent.amount_minor})
        emit(db, store_id, EV_ORDER_PAID, {"order_id": str(order.id), "number": order.number})
        return "processed", None
    if ev.type in ("payment.failed", "payment.cancelled"):
        if intent.status in ("succeeded", "collected"):
            return "processed", "Ignored: payment already captured"
        intent.status = "failed" if ev.type == "payment.failed" else "cancelled"
        intent.failure_reason = (ev.reason or ev.type)[:500]
        timeline(db, order, "payment", ("Payment failed" if ev.type == "payment.failed" else "Payment cancelled")
                 + " — you can try again from your order page", public=True, data={"intent_id": str(intent.id)})
        emit(db, store_id, EV_PAYMENT_FAILED, {"order_id": str(order.id), "number": order.number,
                                               "intent_id": str(intent.id), "type": ev.type})
        return "processed", None
    return "rejected", f"Unsupported event type {ev.type}"


# ---------------------------------------------------------------- sandbox gateway
def sandbox_complete(db: Session, store, intent: PaymentIntent, outcome: str) -> str:
    if payments_mode() != "sandbox":
        raise Forbidden("The sandbox gateway is disabled in live payments mode")
    if intent.provider != "sandbox":
        raise NotFound("Sandbox payment")
    if intent.status != "requires_action":
        raise Conflict("intent_not_open", f"This payment is {intent.status.replace('_', ' ')}")
    order = db.get(Order, intent.order_id)
    intent.simulate = outcome
    if outcome != "timeout":
        intent.status = "processing"
        body = {"id": f"evt_{uuid.uuid4().hex}", "type": {"success": "payment.succeeded", "fail": "payment.failed",
                                                           "cancel": "payment.cancelled"}[outcome],
                "intent_id": str(intent.id), "provider_ref": intent.provider_ref, "amount_minor": intent.amount_minor,
                "currency": intent.currency, "livemode": False, "created": int(time.time()),
                "provider_txn_id": f"SBX{uuid.uuid4().hex[:10].upper()}" if outcome == "success" else None,
                "reason": {"fail": "Simulated decline", "cancel": "Customer cancelled on the hosted page"}.get(outcome)}
        db.add(SandboxWebhookDelivery(store_id=store.id, target="payments", body=json.dumps(body)))
    return f"/order/{order.number}?token={order_access_token(order)}&payment={outcome}"


def sandbox_signed(db: Session, store_id: uuid.UUID, body: str, ts: int | None = None) -> dict[str, str]:
    secret = ensure_secret(db, store_id, SANDBOX_SECRET_REF, "payments.sandbox")
    return {SIGNATURE_HEADER: sign(secret, body, ts), "content-type": "application/json"}


def deliver_sandbox_webhooks(store_id: uuid.UUID | None = None, limit: int = 20) -> int:
    """The sandbox gateway runs in-process: deliver pending signed notifications through the normal webhook path."""
    from ..platform.models import Store

    done = 0
    sf = session_factory()
    with sf() as db:
        q = select(SandboxWebhookDelivery.id).where(SandboxWebhookDelivery.status == "pending",
                                                    SandboxWebhookDelivery.target == "payments",
                                                    SandboxWebhookDelivery.deliver_after <= utcnow())
        if store_id:
            q = q.where(SandboxWebhookDelivery.store_id == store_id)
        ids = list(db.execute(q.order_by(SandboxWebhookDelivery.created_at).limit(limit)).scalars())
    for did in ids:
        with sf() as db:
            d = db.execute(select(SandboxWebhookDelivery).where(SandboxWebhookDelivery.id == did,
                                                                SandboxWebhookDelivery.status == "pending")
                           .with_for_update(skip_locked=True)).scalar_one_or_none()
            if d is None:
                continue
            store = db.get(Store, d.store_id)
            headers = sandbox_signed(db, d.store_id, d.body)  # re-signed per attempt like real gateways
            db.commit()
            try:
                res = handle_webhook(db, store, "sandbox", headers, d.body.encode())
                status, resp = "delivered", f"{res['http']} {res['status']}"
            except Exception as exc:  # noqa: BLE001 - delivery outcome recorded and retried
                db.rollback()
                status, resp = "pending", f"error: {exc}"[:500]
            d = db.get(SandboxWebhookDelivery, did)
            d.attempts += 1
            d.last_response = resp
            d.status = "failed" if status == "pending" and d.attempts >= 6 else status
            if d.status == "pending":
                d.deliver_after = utcnow() + dt.timedelta(seconds=10 * 2 ** d.attempts)
            db.commit()
            done += 1
    return done


@periodic("payments.sandbox_webhooks", every_seconds=5)
def _sandbox_job(db: Session):
    return {"delivered": deliver_sandbox_webhooks()}


# ---------------------------------------------------------------- manual methods
def submit_bank_reference(db: Session, order: Order, reference: str) -> PaymentIntent:
    it = latest_intent(db, order.id)
    if it is None or it.provider != "bank_transfer" or it.status not in ("pending", "awaiting_verification"):
        raise Conflict("not_bank_transfer", "This order is not waiting for a bank transfer")
    it.customer_reference = reference.strip()[:120]
    it.status = "awaiting_verification"
    order.payment_status = "awaiting_verification"
    timeline(db, order, "payment", f"Customer submitted transfer reference {it.customer_reference}", public=True)
    from ..platform.api import notify

    notify(db, order.store_id, "payment.bank_reference", f"Verify bank transfer for {order.number}",
           f"Reference {it.customer_reference}", f"/admin/orders/{order.number}", "orders.write")
    return it


def verify_bank_transfer(db: Session, order: Order, intent: PaymentIntent, actor: Actor, reference: str,
                         amount_minor: int) -> None:
    if intent.provider != "bank_transfer" or intent.status not in ("pending", "awaiting_verification"):
        raise Conflict("not_verifiable", "This payment is not waiting for verification")
    if amount_minor != intent.amount_minor:
        raise Conflict("amount_mismatch", "The received amount must equal the order amount due "
                                          f"(৳{intent.amount_minor / 100:,.2f}). Contact the customer for differences.")
    intent.status = "succeeded"
    intent.captured_minor = amount_minor
    intent.provider_txn_id = reference
    intent.succeeded_at = utcnow()
    add_ledger(db, order.store_id, kind="bank_transfer_received", amount_minor=amount_minor, currency=intent.currency,
               method="bank_transfer", mode="live", order_id=order.id, intent_id=intent.id, reference=reference,
               memo=f"Order {order.number}", actor=actor)
    order.paid_minor += amount_minor
    recompute_payment_status(order)
    timeline(db, order, "payment", "Bank transfer verified", public=True, actor=actor, data={"reference": reference})
    if order.status == "awaiting_payment":
        order.expires_at = None
        transition(db, order, "confirmed", actor, "Bank transfer verified")
    audit(db, order.store_id, actor, "payments.bank_transfer_verified", "order", order.id,
          {"number": order.number, "amount_minor": amount_minor, "reference": reference})
    emit(db, order.store_id, EV_ORDER_PAID, {"order_id": str(order.id), "number": order.number})


def cod_intent(db: Session, order_id: uuid.UUID, lock: bool = True) -> PaymentIntent | None:
    q = select(PaymentIntent).where(PaymentIntent.order_id == order_id, PaymentIntent.provider == "cod",
                                    PaymentIntent.status.in_(["pending", "collected"]))
    if lock:
        q = q.with_for_update()
    return db.execute(q.order_by(PaymentIntent.created_at.desc()).limit(1)).scalar_one_or_none()


def record_cod_collected(db: Session, order: Order, amount_minor: int, courier: str) -> None:
    """The courier collected cash on delivery. Money is with the courier until remitted (ledger at remittance)."""
    it = cod_intent(db, order.id)
    if it is None or amount_minor <= 0:
        return
    amount = min(amount_minor, it.amount_minor - it.captured_minor)
    if amount <= 0:
        return
    it.captured_minor += amount
    if it.captured_minor >= it.amount_minor:
        it.status = "collected"
        it.succeeded_at = utcnow()
    order.paid_minor += amount
    recompute_payment_status(order)
    timeline(db, order, "payment", f"{courier} collected ৳{amount / 100:,.2f} cash on delivery", public=True)


def remit_cod(db: Session, store_id: uuid.UUID, intent_ids: list[uuid.UUID], reference: str, actor: Actor) -> dict:
    total, count = 0, 0
    for it in db.execute(select(PaymentIntent).where(PaymentIntent.store_id == store_id,
                                                     PaymentIntent.id.in_(intent_ids)).with_for_update()).scalars():
        if it.provider != "cod" or it.captured_minor <= 0 or it.remitted_at is not None:
            continue
        it.remitted_at, it.remittance_ref = utcnow(), reference
        add_ledger(db, store_id, kind="cod_collected", amount_minor=it.captured_minor, currency=it.currency,
                   method="cod", mode="live", order_id=it.order_id, intent_id=it.id, reference=reference,
                   memo="COD remittance received", actor=actor)
        total += it.captured_minor
        count += 1
    audit(db, store_id, actor, "payments.cod_remitted", "payment", None,
          {"count": count, "amount_minor": total, "reference": reference})
    return {"remitted": count, "amount_minor": total}


# ---------------------------------------------------------------- refunds
def refund_dict(r: Refund) -> dict:
    return {"id": str(r.id), "amount_minor": r.amount_minor, "currency": r.currency, "status": r.status,
            "method": r.method, "reference": r.reference, "provider_refund_id": r.provider_refund_id,
            "reason": r.reason, "lines": r.lines, "restocked": r.restocked, "failure_reason": r.failure_reason,
            "requested_by": r.requested_by_label, "requested_by_id": str(r.requested_by) if r.requested_by else None,
            "approved_by": r.approved_by_label, "created_at": r.created_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "idempotency_key": r.idempotency_key}


def _refundable(db: Session, intent: PaymentIntent) -> int:
    held = db.execute(select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
        Refund.intent_id == intent.id, Refund.status.in_(["awaiting_approval", "processing", "succeeded"]))
    ).scalar_one()
    return intent.captured_minor - int(held)


def create_refund(db: Session, store_id: uuid.UUID, order_number: str, actor: Actor, *, idempotency_key: str,
                  amount_minor: int, reason: str, lines: list[dict], method: str | None, reference: str | None,
                  intent_id: str | None) -> tuple[Refund, bool]:
    """Returns (refund, needs_execution). Commits the reservation so the provider is called outside row locks."""
    from ..commerce.orders import get_order

    payload = {"order": order_number, "amount": amount_minor, "reason": reason, "lines": lines, "method": method,
               "reference": reference, "intent": intent_id}
    fp = request_fingerprint(payload)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"refund:{store_id}:{idempotency_key}"})
    existing = db.execute(select(Refund).where(Refund.store_id == store_id,
                                               Refund.idempotency_key == idempotency_key)).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fp:
            raise Conflict("idempotency_mismatch", "This idempotency key was already used for a different refund")
        db.commit()
        return existing, False
    order = get_order(db, store_id, order_number, lock=True)
    if intent_id:
        intent = db.execute(select(PaymentIntent).where(PaymentIntent.id == uuid.UUID(intent_id),
                                                        PaymentIntent.order_id == order.id).with_for_update()
                            ).scalar_one_or_none()
    else:
        intent = db.execute(select(PaymentIntent).where(PaymentIntent.order_id == order.id,
                                                        PaymentIntent.captured_minor > PaymentIntent.refunded_minor)
                            .order_by(PaymentIntent.created_at.desc()).limit(1).with_for_update()
                            ).scalar_one_or_none()
    if intent is None or intent.captured_minor <= 0:
        raise Conflict("nothing_to_refund", "This order has no captured payment to refund")
    refundable = _refundable(db, intent)
    if amount_minor <= 0:
        raise AppError("invalid_amount", "Enter an amount greater than zero")
    if amount_minor > refundable:
        raise Conflict("over_refund", f"At most ৳{max(refundable, 0) / 100:,.2f} can still be refunded",
                       details={"refundable_minor": max(refundable, 0)})
    provider = get_provider(intent.provider)
    manual = intent.kind in ("cod", "manual") or provider is None or "refund" not in provider.capabilities
    if manual:
        if method not in MANUAL_PAYOUT_METHODS:
            raise AppError("payout_method_required", "Choose how the money was paid back",
                           details={"methods": MANUAL_PAYOUT_METHODS})
        if not (reference or "").strip():
            raise AppError("payout_reference_required", "Enter the payout reference (transaction id, receipt no.)")
    else:
        method = intent.provider
    by_line = {str(ln.id): ln for ln in lines_of(db, order.id, lock=True)}
    clean_lines = []
    for item in lines or []:
        ln = by_line.get(str(item.get("line_id")))
        q = int(item.get("quantity") or 0)
        if ln is None or q <= 0:
            raise AppError("invalid_line", "A refund line does not belong to this order")
        if q > ln.quantity - ln.refunded_qty:
            raise Conflict("line_over_refund", f"Only {ln.quantity - ln.refunded_qty} of {ln.title} can be refunded")
        if item.get("restock") and q > ln.fulfilled_qty - ln.returned_qty:
            raise Conflict("cannot_restock", f"Only shipped, not yet returned units of {ln.title} can be restocked "
                                             f"({ln.fulfilled_qty - ln.returned_qty})")
        clean_lines.append({"line_id": str(ln.id), "quantity": q, "restock": bool(item.get("restock"))})
    threshold = get_setting(db, store_id, REFUND_APPROVAL_THRESHOLD.key) or 0
    needs_approval = threshold > 0 and amount_minor > threshold
    r = Refund(store_id=store_id, order_id=order.id, intent_id=intent.id, idempotency_key=idempotency_key,
               request_hash=fp, amount_minor=amount_minor, currency=intent.currency,
               status="awaiting_approval" if needs_approval else "processing", method=method,
               reference=(reference or "").strip() or None, reason=reason, lines=clean_lines,
               requested_by=actor.id, requested_by_label=actor.label)
    db.add(r)
    db.flush()
    timeline(db, order, "refund", f"Refund of ৳{amount_minor / 100:,.2f} requested"
             + (" — awaiting second approval" if needs_approval else ""), actor=actor,
             data={"refund_id": str(r.id)})
    audit(db, store_id, actor, "orders.refund_requested", "order", order.id,
          {"refund_id": str(r.id), "amount_minor": amount_minor, "method": method, "needs_approval": needs_approval})
    if needs_approval:
        from ..platform.api import notify

        notify(db, store_id, "refund.approval", f"Refund for {order.number} needs approval",
               f"৳{amount_minor / 100:,.2f} requested by {actor.label}", f"/admin/orders/{order.number}",
               "orders.refund", "warning")
    db.commit()
    return r, not needs_approval


def approve_refund(db: Session, store_id: uuid.UUID, refund_id: uuid.UUID, actor: Actor, approve: bool) -> Refund:
    r = db.execute(select(Refund).where(Refund.id == refund_id, Refund.store_id == store_id).with_for_update()
                   ).scalar_one_or_none()
    if r is None:
        raise NotFound("Refund")
    if r.status != "awaiting_approval":
        raise Conflict("not_awaiting_approval", f"This refund is {r.status.replace('_', ' ')}")
    if actor.id is None or actor.id == r.requested_by:
        raise Forbidden("A different staff member must approve this refund", code="second_approver_required")
    order = db.get(Order, r.order_id)
    r.approved_by, r.approved_by_label = actor.id, actor.label
    r.status = "processing" if approve else "rejected"
    timeline(db, order, "refund", f"Refund of ৳{r.amount_minor / 100:,.2f} {'approved' if approve else 'rejected'}",
             actor=actor, data={"refund_id": str(r.id)})
    audit(db, store_id, actor, "orders.refund_approved" if approve else "orders.refund_rejected", "order", order.id,
          {"refund_id": str(r.id), "amount_minor": r.amount_minor})
    db.commit()
    return r


def execute_refund(db: Session, refund_id: uuid.UUID, actor: Actor) -> Refund:
    """Call the provider once (with the refund's idempotency key) and finalize. Commits."""
    r = db.get(Refund, refund_id)
    if r is None or r.status != "processing" or r.provider_refund_id or r.completed_at:
        return r
    intent = db.get(PaymentIntent, r.intent_id)
    result_status, provider_ref, message = "succeeded", None, None
    if r.method == intent.provider and intent.kind == "redirect":
        provider = get_provider(intent.provider)
        ctx = ctx_for(db, r.store_id, intent.provider, intent.mode)
        try:
            res = provider.refund(ctx, intent, r.amount_minor, r.idempotency_key)
            result_status, provider_ref, message = res.status, res.provider_refund_id, res.message
        except (ProviderError, ProviderUnavailable, AppError) as exc:
            result_status, message = "failed", exc.message
        except Exception as exc:  # noqa: BLE001 - provider transport failure; refund stays failed, money not moved
            result_status, message = "failed", f"Provider error: {exc}"[:500]
    return finalize_refund(db, r.id, result_status, provider_ref, message, actor)


def finalize_refund(db: Session, refund_id: uuid.UUID, status: str, provider_ref: str | None, message: str | None,
                    actor: Actor) -> Refund:
    from ..inventory.ledger import apply_movement

    r = db.execute(select(Refund).where(Refund.id == refund_id).with_for_update()).scalar_one()
    intent = db.execute(select(PaymentIntent).where(PaymentIntent.id == r.intent_id).with_for_update()).scalar_one()
    order = db.execute(select(Order).where(Order.id == r.order_id).with_for_update()).scalar_one()
    if r.status != "processing":
        db.commit()
        return r
    r.provider_refund_id = provider_ref or r.provider_refund_id
    if status == "processing":
        r.failure_reason = message
        timeline(db, order, "refund", f"Refund submitted to {intent.provider}; awaiting provider confirmation",
                 actor=actor)
        db.commit()
        return r
    if status == "failed":
        r.status, r.failure_reason, r.completed_at = "failed", (message or "failed")[:500], utcnow()
        timeline(db, order, "refund", f"Refund failed: {r.failure_reason}", actor=actor)
        audit(db, r.store_id, actor, "orders.refund_failed", "order", order.id, {"refund_id": str(r.id),
                                                                                  "reason": r.failure_reason})
        db.commit()
        return r
    r.status, r.completed_at = "succeeded", utcnow()
    intent.refunded_minor += r.amount_minor
    order.refunded_minor += r.amount_minor
    recompute_payment_status(order)
    add_ledger(db, r.store_id, kind="refund" if r.method == intent.provider else "manual_payout",
               amount_minor=-r.amount_minor, currency=r.currency, method=r.method, mode=intent.mode,
               order_id=order.id, intent_id=intent.id, refund_id=r.id, reference=r.provider_refund_id or r.reference,
               memo=(r.reason or f"Refund on {order.number}")[:300], actor=actor)
    restocked = 0
    for item in r.lines or []:
        ln = db.execute(select(OrderLine).where(OrderLine.id == uuid.UUID(item["line_id"])).with_for_update()
                        ).scalar_one()
        ln.refunded_qty = min(ln.quantity, ln.refunded_qty + item["quantity"])
        if item.get("restock") and ln.variant_id and ln.warehouse_id:
            q = min(item["quantity"], ln.fulfilled_qty - ln.returned_qty)
            if q > 0:
                apply_movement(db, store_id=r.store_id, variant_id=ln.variant_id, warehouse_id=ln.warehouse_id,
                               reason="return", delta_on_hand=q, ref_type="refund", ref_id=str(r.id),
                               note=f"Returned on {order.number}", actor_id=actor.id)
                ln.returned_qty += q
                restocked += q
    r.restocked = restocked > 0
    timeline(db, order, "refund", f"Refunded ৳{r.amount_minor / 100:,.2f} via "
             f"{MANUAL_PAYOUT_METHODS.get(r.method, r.method)}" + (f"; restocked {restocked} unit(s)" if restocked
                                                                  else ""), public=True, actor=actor,
             data={"refund_id": str(r.id), "reference": r.provider_refund_id or r.reference})
    customer_email(db, order, "order.refunded", amount=f"৳{r.amount_minor / 100:,.2f}")
    emit(db, r.store_id, EV_REFUND_SUCCEEDED, {"order_id": str(order.id), "refund_id": str(r.id),
                                               "amount_minor": r.amount_minor})
    emit(db, r.store_id, EV_ORDER_REFUNDED, {"order_id": str(order.id), "number": order.number,
                                             "amount_minor": r.amount_minor})
    audit(db, r.store_id, actor, "orders.refunded", "order", order.id,
          {"refund_id": str(r.id), "amount_minor": r.amount_minor, "method": r.method,
           "provider_refund_id": r.provider_refund_id, "restocked": restocked})
    db.commit()
    return r


# ---------------------------------------------------------------- provider admin
def save_credentials(db: Session, store_id: uuid.UUID, key: str, values: dict[str, str], actor: Actor) -> dict:
    from ...core.security import mask, seal
    from ..platform.models import SecretRecord

    entry = catalog_entry(key)
    allowed = {f["key"] for f in (entry or {}).get("credential_fields", [])}
    if not allowed:
        raise AppError("no_credentials", "This method has no credentials to store")
    unknown = set(values) - allowed
    if unknown:
        raise AppError("unknown_fields", f"Unknown credential fields: {', '.join(sorted(unknown))}")
    cfg = method_config(db, store_id, key, create=True)
    for name, value in values.items():
        if not value:
            continue
        ref = f"payments.{key}.{name}"
        rec = db.execute(select(SecretRecord).where(SecretRecord.store_id == store_id, SecretRecord.ref == ref)
                         ).scalar_one_or_none()
        if rec is None:
            db.add(SecretRecord(store_id=store_id, ref=ref, sealed=seal(value), hint=mask(value), owner_kind="core",
                                owner_ref=f"payments.{key}"))
        else:
            rec.sealed, rec.hint, rec.rotated_at = seal(value), mask(value), utcnow()
    cfg.credential_fields = sorted(set(cfg.credential_fields or []) | {k for k, v in values.items() if v})
    cfg.verification = {"state": "unverified", "message": "Credentials changed; verify again.",
                        "checked_at": utcnow().isoformat()}
    cfg.enabled = cfg.enabled and state_for(entry, cfg) not in ("adapter_draft_unverified", "catalog_only")
    audit(db, store_id, actor, "payments.credentials_saved", "payment_method", key, {"fields": sorted(values)})
    return credential_hints(db, store_id, key)


def credential_hints(db: Session, store_id: uuid.UUID, key: str) -> dict:
    from ..platform.models import SecretRecord

    rows = db.execute(select(SecretRecord).where(SecretRecord.store_id == store_id,
                                                 SecretRecord.ref.startswith(f"payments.{key}."))).scalars()
    return {r.ref.split(".", 2)[2]: r.hint or "••••" for r in rows}


def verify_method(db: Session, store_id: uuid.UUID, key: str, actor: Actor) -> dict:
    entry = catalog_entry(key)
    if entry is None:
        raise NotFound("Payment method")
    cfg = method_config(db, store_id, key, create=True)
    provider = get_provider(key)
    now = utcnow().isoformat()
    if entry["base_state"] == "catalog_only" or provider is None:
        result = {"ok": False, "state": "catalog_only",
                  "message": "Not implemented: VOLTORA has no adapter for this provider yet, so nothing can be verified."}
    elif entry["base_state"] == "built_in":
        result = {"ok": True, "state": "built_in", "message": "Built-in method; no third party to verify."}
    else:
        missing = [f["key"] for f in entry.get("credential_fields", []) if f["key"] not in (cfg.credential_fields or [])]
        if missing:
            result = {"ok": False, "state": state_for(entry, cfg),
                      "message": f"Save these credentials first: {', '.join(missing)}"}
        else:
            ctx = ctx_for(db, store_id, key)
            try:
                ok, msg = provider.verify_credentials(ctx)
            except (ProviderUnavailable, ProviderError) as exc:
                ok, msg = False, exc.message
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, f"Verification failed: {exc}"[:300]
            state = (f"{ctx.mode}_verified" if ok and entry["base_state"] == "adapter_draft_unverified"
                     else entry["base_state"] if not ok else "simulation")
            result = {"ok": ok, "state": state, "message": msg}
            if entry["base_state"] == "adapter_draft_unverified":
                cfg.verification = {"state": state if ok else "unverified", "message": msg, "checked_at": now,
                                    "mode": ctx.mode}
    audit(db, store_id, actor, "payments.verify", "payment_method", key, {"ok": result["ok"],
                                                                          "state": result["state"]})
    return {**result, "checked_at": now}
