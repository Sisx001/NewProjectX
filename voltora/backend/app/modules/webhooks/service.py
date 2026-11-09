"""Signed webhook delivery: outbox fan-out, retries with exponential backoff, dead-letter, replay and ping."""
from __future__ import annotations

import datetime as dt
import json
import secrets as pysecrets
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...core.errors import AppError
from ...core.security import seal, unseal
from ...core.services import _canon, on, periodic
from ...db import utcnow
from .models import WebhookDelivery, WebhookEndpoint
from .outbound import SIGNATURE_HEADER, request, scrub, sign

MAX_ATTEMPTS = 8
HOLD_MINUTES = 10
# Only non-personal keys are copied into chat-style notifications (Slack/Telegram are third parties).
SAFE_SUMMARY_KEYS = ("number", "order_number", "status", "payment_status", "fulfillment_status", "total_minor",
                     "currency", "sku", "title", "quantity", "available", "reason", "courier", "tracking_code")


def event_matches(patterns: list, event_type: str) -> bool:
    for p in patterns or []:
        if p == "*" or p == event_type or (p.endswith(".*") and event_type.startswith(p[:-1])):
            return True
    return False


def new_secret(db: Session, store_id: uuid.UUID, ref: str, owner_kind: str = "core", owner_ref: str | None = None,
               value: str | None = None) -> str:
    from ..platform.models import SecretRecord

    raw = value or "whsec_" + pysecrets.token_urlsafe(32)
    rec = db.execute(select(SecretRecord).where(SecretRecord.store_id == store_id, SecretRecord.ref == ref)
                     ).scalar_one_or_none()
    if rec is None:
        db.add(SecretRecord(store_id=store_id, ref=ref, sealed=seal(raw), hint=raw[-4:], owner_kind=owner_kind,
                            owner_ref=owner_ref))
    else:
        rec.sealed, rec.hint, rec.rotated_at = seal(raw), raw[-4:], utcnow()
    return raw


def read_secret(db: Session, store_id: uuid.UUID, ref: str) -> str | None:
    from ..platform.models import SecretRecord

    rec = db.execute(select(SecretRecord).where(SecretRecord.store_id == store_id, SecretRecord.ref == ref)
                     ).scalar_one_or_none()
    return unseal(rec.sealed) if rec else None


def enqueue(db: Session, ep: WebhookEndpoint, event_type: str, payload: dict, event_id: uuid.UUID | None = None,
            source: str = "event", replay_of: uuid.UUID | None = None) -> uuid.UUID | None:
    """Idempotent per (endpoint, outbox event): a redelivered event never creates a second delivery."""
    stmt = insert(WebhookDelivery).values(
        id=uuid.uuid4(), store_id=ep.store_id, endpoint_id=ep.id, event_id=event_id, event_type=event_type,
        payload=json.loads(_canon(payload)), status="pending", attempts=0, next_attempt_at=utcnow(),
        source=source, replay_of=replay_of, created_at=utcnow(),
    ).on_conflict_do_nothing(index_elements=["endpoint_id", "event_id"]).returning(WebhookDelivery.id)
    return db.execute(stmt).scalar()


@on("*", name="webhooks.fanout")
def fanout(db: Session, ev) -> dict:
    if ev.store_id is None:
        return {"queued": 0}
    eps = db.execute(select(WebhookEndpoint).where(WebhookEndpoint.store_id == ev.store_id,
                                                   WebhookEndpoint.enabled)).scalars().all()
    n = 0
    for ep in eps:
        if event_matches(ep.events, ev.type) and enqueue(db, ep, ev.type, ev.payload or {}, event_id=ev.id):
            n += 1
    return {"queued": n}


def summary_text(event_type: str, payload: dict) -> str:
    bits = [f"{k}: {payload[k]}" for k in SAFE_SUMMARY_KEYS if k in payload and payload[k] is not None]
    return f"[VOLTORA] {event_type}" + (" — " + ", ".join(bits) if bits else "")


def _build(db: Session, ep: WebhookEndpoint, d: WebhookDelivery) -> tuple[str, bytes, dict, list[str], list]:
    """Returns url, body, headers, allowed_hosts, secrets_to_scrub."""
    scrub_values: list[str | None] = []
    url = ep.url
    chat_id = None
    if ep.owner_kind == "plugin":
        from ..plugins.service import resolve_endpoint_target

        url, chat_id, scrub_values = resolve_endpoint_target(db, ep)
    if not url:
        raise AppError("webhook_unconfigured", "Destination URL is not configured")
    if ep.format == "slack":
        body = json.dumps({"text": summary_text(d.event_type, d.payload)}).encode()
    elif ep.format == "telegram":
        body = json.dumps({"chat_id": chat_id, "text": summary_text(d.event_type, d.payload)}).encode()
    else:
        body = _canon({"id": str(d.id), "type": d.event_type, "created_at": d.created_at.isoformat(),
                       "data": d.payload}).encode()
    secret = read_secret(db, ep.store_id, ep.secret_ref) or ""
    sig, ts = sign(secret, body)
    headers = {"Content-Type": "application/json", "User-Agent": "VOLTORA-Webhooks/1",
               "X-Voltora-Event": d.event_type, "X-Voltora-Delivery": str(d.id), "X-Voltora-Timestamp": str(ts)}
    if ep.format == "voltora":
        headers[SIGNATURE_HEADER] = sig
    return url, body, headers, ep.allowed_hosts, [secret, *scrub_values]


def deliver(db: Session, d: WebhookDelivery) -> WebhookDelivery:
    ep = db.get(WebhookEndpoint, d.endpoint_id)
    d.attempts += 1
    if ep is None or not ep.enabled:
        d.status, d.error = "dead", "Endpoint disabled or removed"
        return d
    secrets_: list = []
    try:
        url, body, headers, allowed, secrets_ = _build(db, ep, d)
        r = request("POST", url, content=body, headers=headers, timeout=10.0, allowed_hosts=allowed)
        d.response_status, d.duration_ms = r.status, r.ms
        d.response_body = scrub(r.text[:1000], *secrets_)
        ok = 200 <= r.status < 300
        d.error = None if ok else f"HTTP {r.status}"
    except AppError as exc:
        ok = False
        d.error = scrub(exc.message, *secrets_)
        if exc.code == "network_disabled":
            d.status, d.attempts = "held", d.attempts - 1
            d.next_attempt_at = utcnow() + dt.timedelta(minutes=HOLD_MINUTES)
            return d
    except httpx.HTTPError as exc:
        ok = False
        d.error = scrub(f"{type(exc).__name__}: {exc}", *secrets_)[:500]
    ep.last_delivery_at = utcnow()
    if ok:
        d.status, d.delivered_at = "succeeded", utcnow()
        ep.consecutive_failures, ep.last_status = 0, "succeeded"
    else:
        ep.consecutive_failures += 1
        ep.last_status = "failed"
        if d.attempts >= MAX_ATTEMPTS:
            d.status = "dead"
        else:
            d.status = "failed"
            d.next_attempt_at = utcnow() + dt.timedelta(seconds=min(3600, 30 * 2 ** (d.attempts - 1)))
    return d


@periodic("webhooks.deliver", every_seconds=5)
def deliver_due(db: Session) -> dict:
    rows = db.execute(select(WebhookDelivery).where(WebhookDelivery.status.in_(["pending", "failed", "held"]),
                                                    WebhookDelivery.next_attempt_at <= utcnow())
                      .order_by(WebhookDelivery.next_attempt_at).limit(20).with_for_update(skip_locked=True)
                      ).scalars().all()
    for d in rows:
        deliver(db, d)
    return {"processed": len(rows)}
