"""Payment APIs: sandbox hosted page, provider webhooks/returns, admin methods/credentials, refunds, finance views."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.deps import Principal, current_store, parse_uuid, principal, require
from ...core.errors import AppError, NotFound
from ...core.services import audit, limiter
from ...db import get_db
from ..commerce.models import Order
from ..commerce.orders import get_order, order_access_token
from . import service as svc
from .models import LedgerEntry, PaymentEvent, PaymentIntent, PaymentMethodConfig, Refund, SandboxWebhookDelivery
from .providers import get_provider
from .providers.catalog import CATALOG, enableable, state_for

router = APIRouter(prefix="/api", tags=["payments"])
MODULE = {"title": "Payments", "description": "Payment methods, provider adapters, webhooks, refunds, money ledger"}


# ================================================================ sandbox hosted page (simulation)
@router.get("/payments/sandbox/{intent_id}")
def sandbox_page(intent_id: str, db: Session = Depends(get_db), p: Principal = Depends(principal)):
    it = db.get(PaymentIntent, parse_uuid(intent_id, "Sandbox payment"))
    if it is None or it.provider != "sandbox" or it.store_id != p.store.id:
        raise NotFound("Sandbox payment")
    o = db.get(Order, it.order_id)
    return {"intent_id": str(it.id), "amount_minor": it.amount_minor, "currency": it.currency,
            "order_number": o.number, "merchant": p.store.name, "status": it.status, "simulation": True,
            "notice": "VOLTORA Sandbox Gateway — a simulation for testing. No real money moves.",
            "outcomes": ["success", "fail", "cancel", "timeout"]}


class SandboxCompleteIn(BaseModel):
    outcome: str = Field(pattern="^(success|fail|cancel|timeout)$")


@router.post("/payments/sandbox/{intent_id}/complete")
def sandbox_complete(intent_id: str, body: SandboxCompleteIn, db: Session = Depends(get_db),
                     p: Principal = Depends(principal)):
    limiter.hit(f"sandbox-pay:{p.ip}", 30, 60)
    it = db.execute(select(PaymentIntent).where(PaymentIntent.id == parse_uuid(intent_id, "Sandbox payment"),
                                                PaymentIntent.store_id == p.store.id).with_for_update()
                    ).scalar_one_or_none()
    if it is None:
        raise NotFound("Sandbox payment")
    redirect = svc.sandbox_complete(db, p.store, it, body.outcome)
    db.commit()
    svc.deliver_sandbox_webhooks(p.store.id)
    return {"redirect_url": redirect, "note": "The order is updated only by the signed webhook, not this redirect."}


# ================================================================ provider callbacks
@router.post("/payments/webhooks/{provider}")
async def webhook(provider: str, request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > 256 * 1024:
        return JSONResponse({"error": {"code": "too_large", "message": "Payload too large"}}, status_code=413)
    store = current_store(db)
    headers = {k.lower(): v for k, v in request.headers.items()}
    res = svc.handle_webhook(db, store, provider, headers, raw)
    code = res.pop("http")
    return JSONResponse(res, status_code=code)


@router.get("/payments/return/{provider}/{intent_id}")
def provider_return(provider: str, intent_id: str, request: Request, db: Session = Depends(get_db)):
    """Customer returns from a hosted page. We confirm server-to-server; the query string is never trusted."""
    store = current_store(db)
    it = db.get(PaymentIntent, parse_uuid(intent_id, "Payment"))
    if it is None or it.provider != provider or it.store_id != store.id:
        raise NotFound("Payment")
    order = db.get(Order, it.order_id)
    target = f"/order/{order.number}?token={order_access_token(order)}"
    p = get_provider(provider)
    try:
        ev = p.handle_return(svc.ctx_for(db, store.id, provider, it.mode), it, dict(request.query_params))
        if ev is not None:
            svc.process_event(db, store.id, provider, ev)
    except AppError as exc:
        db.rollback()
        return RedirectResponse(f"{target}&payment=pending&reason={exc.code}", status_code=303)
    return RedirectResponse(f"{target}&payment=returned", status_code=303)


# ================================================================ admin: methods & providers
def _method_out(db: Session, store_id, entry: dict, cfg: PaymentMethodConfig | None) -> dict:
    provider = get_provider(entry["key"])
    ok, reason = enableable(entry, cfg, svc.payments_mode())
    return {"key": entry["key"], "label": entry["label"], "kind": entry["kind"], "region": entry.get("region"),
            "docs_url": entry.get("docs_url"), "notes": entry.get("notes"), "state": state_for(entry, cfg),
            "has_adapter": provider is not None, "simulation": bool(provider and provider.simulation),
            "capabilities": sorted(provider.capabilities) if provider else [],
            "credential_fields": entry.get("credential_fields", []),
            "credentials": svc.credential_hints(db, store_id, entry["key"]) if entry.get("credential_fields") else {},
            "can_enable": ok, "cannot_enable_reason": reason,
            "config": None if cfg is None else {
                "enabled": cfg.enabled, "label": cfg.label, "description": cfg.description,
                "fee_minor": cfg.fee_minor, "min_order_minor": cfg.min_order_minor,
                "max_order_minor": cfg.max_order_minor, "position": cfg.position, "mode": cfg.mode,
                "settings": cfg.settings, "verification": cfg.verification}}


@router.get("/admin/payments/methods")
def list_methods(db: Session = Depends(get_db), p: Principal = Depends(require("payments.read"))):
    cfgs = {c.key: c for c in db.execute(select(PaymentMethodConfig).where(
        PaymentMethodConfig.store_id == p.store.id)).scalars()}
    entries = list(CATALOG) + [svc.catalog_entry(k) for k in cfgs if k not in {c["key"] for c in CATALOG}
                               and svc.catalog_entry(k)]
    return {"items": [_method_out(db, p.store.id, e, cfgs.get(e["key"])) for e in entries],
            "payments_mode": svc.payments_mode(),
            "external_network": __import__("app.config", fromlist=["get_settings"]).get_settings().external_network}


class MethodPatch(BaseModel):
    enabled: bool | None = None
    label: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    fee_minor: int | None = Field(default=None, ge=0, le=100_000_00)
    min_order_minor: int | None = Field(default=None, ge=0)
    max_order_minor: int | None = Field(default=None, ge=0)
    position: int | None = Field(default=None, ge=0, le=1000)
    settings: dict[str, str] | None = None


BANK_SETTING_KEYS = {"instructions", "bank_name", "account_name", "account_number", "branch", "routing_number"}


@router.patch("/admin/payments/methods/{key}")
def patch_method(key: str, body: MethodPatch, db: Session = Depends(get_db),
                 p: Principal = Depends(require("payments.manage"))):
    entry = svc.catalog_entry(key)
    if entry is None:
        raise NotFound("Payment method")
    cfg = svc.method_config(db, p.store.id, key, create=True)
    changes = body.model_dump(exclude_none=True)
    if changes.get("enabled"):
        ok, reason = enableable(entry, cfg, svc.payments_mode())
        if not ok:
            raise AppError("cannot_enable", reason, status=409)
        if key == "bank_transfer" and not ((body.settings or cfg.settings or {}).get("instructions") or "").strip():
            raise AppError("instructions_required", "Add transfer instructions before enabling bank transfer",
                           details={"field": "settings.instructions"})
    if body.settings is not None:
        allowed = BANK_SETTING_KEYS if key == "bank_transfer" else set()
        unknown = set(body.settings) - allowed
        if unknown:
            raise AppError("unknown_settings", f"Unknown settings: {', '.join(sorted(unknown))}")
        cfg.settings = {**(cfg.settings or {}), **{k: v.strip()[:2000] for k, v in body.settings.items()}}
    for k in ("enabled", "label", "description", "fee_minor", "min_order_minor", "max_order_minor", "position"):
        if k in changes:
            setattr(cfg, k, changes[k])
    if body.max_order_minor == 0:
        cfg.max_order_minor = None
    audit(db, p.store.id, p.actor, "payments.method_updated", "payment_method", key,
          {k: v for k, v in changes.items() if k != "settings"})
    db.commit()
    return _method_out(db, p.store.id, entry, cfg)


class CredentialsIn(BaseModel):
    values: dict[str, str]


@router.put("/admin/payments/methods/{key}/credentials")
def put_credentials(key: str, body: CredentialsIn, db: Session = Depends(get_db),
                    p: Principal = Depends(require("payments.manage"))):
    hints = svc.save_credentials(db, p.store.id, key, body.values, p.actor)
    db.commit()
    return {"credentials": hints, "verification": {"state": "unverified"}}


@router.post("/admin/payments/methods/{key}/verify")
def verify_method(key: str, db: Session = Depends(get_db), p: Principal = Depends(require("payments.manage"))):
    limiter.hit(f"verify:{p.store.id}:{key}", 10, 600)
    res = svc.verify_method(db, p.store.id, key, p.actor)
    db.commit()
    return res


# ================================================================ admin: order money actions
class RefundIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=100)
    amount_minor: int = Field(gt=0)
    reason: str = Field(default="", max_length=500)
    lines: list[dict] = Field(default_factory=list, max_length=100)
    method: str | None = Field(default=None, max_length=40)
    reference: str | None = Field(default=None, max_length=200)
    intent_id: str | None = None


@router.post("/admin/orders/{number}/refunds")
def create_refund(number: str, body: RefundIn, db: Session = Depends(get_db),
                  p: Principal = Depends(require("orders.refund"))):
    r, run = svc.create_refund(db, p.store.id, number, p.actor, idempotency_key=body.idempotency_key,
                               amount_minor=body.amount_minor, reason=body.reason, lines=body.lines,
                               method=body.method, reference=body.reference, intent_id=body.intent_id)
    if run:
        r = svc.execute_refund(db, r.id, p.actor)
    return svc.refund_dict(r)


@router.post("/admin/refunds/{refund_id}/approve")
def approve_refund(refund_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("orders.refund"))):
    r = svc.approve_refund(db, p.store.id, parse_uuid(refund_id, "Refund"), p.actor, True)
    r = svc.execute_refund(db, r.id, p.actor)
    return svc.refund_dict(r)


@router.post("/admin/refunds/{refund_id}/reject")
def reject_refund(refund_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("orders.refund"))):
    return svc.refund_dict(svc.approve_refund(db, p.store.id, parse_uuid(refund_id, "Refund"), p.actor, False))


class VerifyTransferIn(BaseModel):
    reference: str = Field(min_length=2, max_length=120)
    amount_minor: int = Field(gt=0)


@router.post("/admin/orders/{number}/payments/{intent_id}/verify-transfer")
def verify_transfer(number: str, intent_id: str, body: VerifyTransferIn, db: Session = Depends(get_db),
                    p: Principal = Depends(require("orders.write"))):
    from ..commerce.api import admin_order_detail

    o = get_order(db, p.store.id, number, lock=True)
    it = db.execute(select(PaymentIntent).where(PaymentIntent.id == parse_uuid(intent_id, "Payment"),
                                                PaymentIntent.order_id == o.id).with_for_update()).scalar_one_or_none()
    if it is None:
        raise NotFound("Payment")
    svc.verify_bank_transfer(db, o, it, p.actor, body.reference.strip(), body.amount_minor)
    db.commit()
    return admin_order_detail(db, p, o)


class RemitIn(BaseModel):
    intent_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    reference: str = Field(min_length=2, max_length=120)


@router.post("/admin/payments/cod/remit")
def cod_remit(body: RemitIn, db: Session = Depends(get_db), p: Principal = Depends(require("payments.manage"))):
    res = svc.remit_cod(db, p.store.id, body.intent_ids, body.reference.strip(), p.actor)
    db.commit()
    return res


# ================================================================ admin: finance views
def _since(days: int | None):
    from ...db import utcnow

    return utcnow() - dt.timedelta(days=days) if days else None


@router.get("/admin/payments/transactions")
def transactions(status: str | None = None, provider: str | None = None, page: int = Query(1, ge=1),
                 page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                 p: Principal = Depends(require("payments.read"))):
    conds = [PaymentIntent.store_id == p.store.id]
    if status:
        conds.append(PaymentIntent.status.in_(status.split(",")))
    if provider:
        conds.append(PaymentIntent.provider == provider)
    total = db.execute(select(func.count()).select_from(PaymentIntent).where(*conds)).scalar_one()
    rows = db.execute(select(PaymentIntent, Order.number).join(Order, Order.id == PaymentIntent.order_id)
                      .where(*conds).order_by(PaymentIntent.created_at.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"id": str(i.id), "order_number": n, "provider": i.provider, "kind": i.kind, "mode": i.mode,
                       "amount_minor": i.amount_minor, "captured_minor": i.captured_minor,
                       "refunded_minor": i.refunded_minor, "currency": i.currency, "status": i.status,
                       "provider_ref": i.provider_ref, "provider_txn_id": i.provider_txn_id,
                       "failure_reason": i.failure_reason, "created_at": i.created_at.isoformat()} for i, n in rows],
            "total": total, "page": page, "page_size": page_size}


@router.get("/admin/payments/ledger")
def ledger(kind: str | None = None, days: int | None = Query(None, ge=1, le=3650), page: int = Query(1, ge=1),
           page_size: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
           p: Principal = Depends(require("payments.read"))):
    conds = [LedgerEntry.store_id == p.store.id]
    if kind:
        conds.append(LedgerEntry.kind.in_(kind.split(",")))
    since = _since(days)
    if since:
        conds.append(LedgerEntry.created_at >= since)
    total = db.execute(select(func.count()).select_from(LedgerEntry).where(*conds)).scalar_one()
    sums = db.execute(select(LedgerEntry.kind, LedgerEntry.mode, func.sum(LedgerEntry.amount_minor))
                      .where(*conds).group_by(LedgerEntry.kind, LedgerEntry.mode)).all()
    orders = {o.id: o.number for o in db.execute(select(Order).where(Order.store_id == p.store.id)).scalars()} \
        if total else {}
    rows = db.execute(select(LedgerEntry).where(*conds).order_by(LedgerEntry.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).scalars()
    return {"items": [{"id": e.id, "kind": e.kind, "amount_minor": e.amount_minor, "currency": e.currency,
                       "method": e.method, "mode": e.mode, "reference": e.reference, "memo": e.memo,
                       "order_number": orders.get(e.order_id), "created_at": e.created_at.isoformat()} for e in rows],
            "total": total, "page": page, "page_size": page_size,
            "totals": [{"kind": k, "mode": m, "amount_minor": int(s)} for k, m, s in sums],
            "net_live_minor": int(sum(s for _, m, s in sums if m == "live"))}


@router.get("/admin/payments/reconciliation")
def reconciliation(db: Session = Depends(get_db), p: Principal = Depends(require("payments.read"))):
    """Online intents vs provider events: flags anything that needs a human."""
    sid = p.store.id
    issues = []
    rows = db.execute(select(PaymentIntent, Order).join(Order, Order.id == PaymentIntent.order_id)
                      .where(PaymentIntent.store_id == sid, PaymentIntent.kind == "redirect")
                      .order_by(PaymentIntent.created_at.desc()).limit(500)).all()
    ev_counts = dict(db.execute(select(PaymentEvent.intent_id, func.count()).where(
        PaymentEvent.store_id == sid, PaymentEvent.status == "processed").group_by(PaymentEvent.intent_id)).all())
    for it, o in rows:
        problem = None
        if it.status == "succeeded" and o.status == "cancelled" and o.refunded_minor < o.paid_minor:
            problem = "Paid after cancellation — refund required"
        elif it.status == "succeeded" and not ev_counts.get(it.id):
            problem = "Captured without a processed provider event"
        elif it.status == "processing":
            problem = "Customer returned from the hosted page; waiting for the provider's signed notification"
        if problem:
            issues.append({"intent_id": str(it.id), "order_number": o.number, "provider": it.provider,
                           "status": it.status, "amount_minor": it.amount_minor, "problem": problem,
                           "created_at": it.created_at.isoformat()})
    events = db.execute(select(PaymentEvent).where(PaymentEvent.store_id == sid)
                        .order_by(PaymentEvent.received_at.desc()).limit(100)).scalars()
    deliveries = db.execute(select(SandboxWebhookDelivery).where(SandboxWebhookDelivery.store_id == sid,
                                                                 SandboxWebhookDelivery.status != "delivered")
                            .order_by(SandboxWebhookDelivery.created_at.desc()).limit(50)).scalars()
    summary = dict(db.execute(select(PaymentEvent.status, func.count()).where(PaymentEvent.store_id == sid)
                              .group_by(PaymentEvent.status)).all())
    return {"issues": issues, "event_summary": summary,
            "events": [{"id": str(e.id), "provider": e.provider, "event_id": e.provider_event_id, "type": e.type,
                        "status": e.status, "signature_valid": e.signature_valid, "reason": e.reason,
                        "amount_minor": e.amount_minor, "attempts": e.attempts,
                        "intent_id": str(e.intent_id) if e.intent_id else None,
                        "received_at": e.received_at.isoformat()} for e in events],
            "pending_sandbox_deliveries": [{"id": str(d.id), "status": d.status, "attempts": d.attempts,
                                            "last_response": d.last_response} for d in deliveries],
            "refunds_in_flight": [svc.refund_dict(r) for r in db.execute(select(Refund).where(
                Refund.store_id == sid, Refund.status.in_(["processing", "awaiting_approval"]))).scalars()]}
