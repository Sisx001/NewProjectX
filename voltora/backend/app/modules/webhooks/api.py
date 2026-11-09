from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, require
from ...core.errors import AppError, Conflict, NotFound
from ...core.services import EVENT_TYPES, audit
from ...db import get_db
from .models import WebhookDelivery, WebhookEndpoint
from .outbound import validate_url
from .service import deliver, enqueue, new_secret

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
MODULE = {"title": "Webhooks", "description": "Signed outbound event delivery with retries, dead-letter and replay"}
DEV = require("developer.manage")


def _validate_events(events: list[str]) -> list[str]:
    out = []
    for e in events:
        e = e.strip()
        if not e:
            continue
        if e != "*" and not e.endswith(".*") and e not in EVENT_TYPES:
            raise AppError("unknown_event", f"Unknown event type {e}", details={"known": sorted(EVENT_TYPES)})
        out.append(e)
    if not out:
        raise AppError("events_required", "Choose at least one event")
    return sorted(set(out))


def ep_out(ep: WebhookEndpoint, hint: str | None = None) -> dict:
    return {"id": str(ep.id), "name": ep.name, "description": ep.description, "url": ep.url, "events": ep.events,
            "format": ep.format, "enabled": ep.enabled, "owner_kind": ep.owner_kind, "plugin_id": ep.plugin_id,
            "consecutive_failures": ep.consecutive_failures, "last_status": ep.last_status,
            "last_delivery_at": ep.last_delivery_at.isoformat() if ep.last_delivery_at else None,
            "secret_hint": hint, "created_at": ep.created_at.isoformat()}


def d_out(d: WebhookDelivery, full: bool = False) -> dict:
    out = {"id": str(d.id), "endpoint_id": str(d.endpoint_id), "event_id": str(d.event_id) if d.event_id else None,
           "event_type": d.event_type, "status": d.status, "attempts": d.attempts, "source": d.source,
           "response_status": d.response_status, "error": d.error, "duration_ms": d.duration_ms,
           "next_attempt_at": d.next_attempt_at.isoformat(), "created_at": d.created_at.isoformat(),
           "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
           "replay_of": str(d.replay_of) if d.replay_of else None}
    if full:
        out |= {"payload": d.payload, "response_body": d.response_body}
    return out


def _get_ep(db: Session, p: Principal, ep_id: str) -> WebhookEndpoint:
    ep = db.get(WebhookEndpoint, parse_uuid(ep_id, "Webhook endpoint"))
    if ep is None or ep.store_id != p.store.id:
        raise NotFound("Webhook endpoint")
    return ep


class EndpointIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    url: str = Field(max_length=800)
    events: list[str] = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=1000)


class EndpointPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    url: str | None = Field(None, max_length=800)
    events: list[str] | None = None
    description: str | None = Field(None, max_length=1000)
    enabled: bool | None = None


@router.get("/event-types")
def event_types(p: Principal = Depends(DEV)):
    return {"items": [{"type": k, "description": v} for k, v in sorted(EVENT_TYPES.items())]}


@router.get("/endpoints")
def list_endpoints(db: Session = Depends(get_db), p: Principal = Depends(DEV)):
    from ..platform.models import SecretRecord

    rows = db.execute(select(WebhookEndpoint).where(WebhookEndpoint.store_id == p.store.id)
                      .order_by(WebhookEndpoint.created_at.desc())).scalars().all()
    hints = dict(db.execute(select(SecretRecord.ref, SecretRecord.hint).where(
        SecretRecord.store_id == p.store.id, SecretRecord.ref.in_([r.secret_ref for r in rows]))).all())
    return {"items": [ep_out(r, hints.get(r.secret_ref)) for r in rows]}


@router.post("/endpoints")
def create_endpoint(body: EndpointIn, db: Session = Depends(get_db), p: Principal = Depends(DEV)):
    validate_url(body.url)
    ep = WebhookEndpoint(id=uuid.uuid4(), store_id=p.store.id, name=body.name, description=body.description,
                         url=body.url, events=_validate_events(body.events), secret_ref="")
    ep.secret_ref = f"webhooks.{ep.id}.signing"
    raw = new_secret(db, p.store.id, ep.secret_ref, owner_kind="core", owner_ref="webhooks")
    db.add(ep)
    db.flush()
    audit(db, p.store.id, p.actor, "webhooks.endpoint_created", "webhook_endpoint", ep.id,
          {"url": body.url, "events": ep.events})
    db.commit()
    return {**ep_out(ep, raw[-4:]), "secret": raw,
            "note": "Copy the signing secret now; it will not be shown again."}


@router.patch("/endpoints/{ep_id}")
def update_endpoint(ep_id: str, body: EndpointPatch, db: Session = Depends(get_db), p: Principal = Depends(DEV)):
    ep = _get_ep(db, p, ep_id)
    if ep.owner_kind == "plugin" and (body.url is not None or body.events is not None):
        raise Conflict("managed_by_extension", f"This endpoint is managed by extension {ep.plugin_id}")
    changes = body.model_dump(exclude_none=True)
    if body.url is not None:
        validate_url(body.url)
    if body.events is not None:
        changes["events"] = _validate_events(body.events)
    for k, v in changes.items():
        setattr(ep, k, v)
    if body.enabled:
        ep.consecutive_failures = 0
    audit(db, p.store.id, p.actor, "webhooks.endpoint_updated", "webhook_endpoint", ep.id, changes)
    db.commit()
    return ep_out(ep)


@router.delete("/endpoints/{ep_id}")
def delete_endpoint(ep_id: str, db: Session = Depends(get_db), p: Principal = Depends(DEV)):
    from ..platform.models import SecretRecord

    ep = _get_ep(db, p, ep_id)
    if ep.owner_kind == "plugin":
        raise Conflict("managed_by_extension", f"Disable or uninstall extension {ep.plugin_id} instead")
    sec = db.execute(select(SecretRecord).where(SecretRecord.store_id == p.store.id,
                                                SecretRecord.ref == ep.secret_ref)).scalar_one_or_none()
    if sec:
        db.delete(sec)
    db.delete(ep)
    audit(db, p.store.id, p.actor, "webhooks.endpoint_deleted", "webhook_endpoint", ep.id)
    db.commit()
    return {"ok": True}


@router.post("/endpoints/{ep_id}/rotate-secret")
def rotate_secret(ep_id: str, db: Session = Depends(get_db), p: Principal = Depends(DEV)):
    ep = _get_ep(db, p, ep_id)
    if ep.owner_kind == "plugin":
        raise Conflict("managed_by_extension", "Rotate the extension's signing secret from Extensions")
    raw = new_secret(db, p.store.id, ep.secret_ref)
    audit(db, p.store.id, p.actor, "webhooks.secret_rotated", "webhook_endpoint", ep.id)
    db.commit()
    return {"secret": raw, "secret_hint": raw[-4:]}


@router.post("/endpoints/{ep_id}/ping")
def ping(ep_id: str, db: Session = Depends(get_db), p: Principal = Depends(DEV)):
    ep = _get_ep(db, p, ep_id)
    did = enqueue(db, ep, "voltora.ping", {"message": "Test delivery from VOLTORA"}, source="ping")
    d = deliver(db, db.get(WebhookDelivery, did))
    audit(db, p.store.id, p.actor, "webhooks.ping", "webhook_endpoint", ep.id, {"status": d.status})
    db.commit()
    return d_out(d, full=True)


@router.get("/deliveries")
def list_deliveries(endpoint_id: str | None = None, status: str | None = None, page: int = Query(1, ge=1),
                    db: Session = Depends(get_db), p: Principal = Depends(DEV)):
    q = select(WebhookDelivery).where(WebhookDelivery.store_id == p.store.id)
    if endpoint_id:
        q = q.where(WebhookDelivery.endpoint_id == parse_uuid(endpoint_id, "Webhook endpoint"))
    if status:
        q = q.where(WebhookDelivery.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(WebhookDelivery.created_at.desc()).offset((page - 1) * 50).limit(50)).scalars()
    return {"items": [d_out(d) for d in rows], "total": total, "page": page, "page_size": 50}


@router.get("/deliveries/{delivery_id}")
def get_delivery(delivery_id: str, db: Session = Depends(get_db), p: Principal = Depends(DEV)):
    d = db.get(WebhookDelivery, parse_uuid(delivery_id, "Delivery"))
    if d is None or d.store_id != p.store.id:
        raise NotFound("Delivery")
    return d_out(d, full=True)


@router.post("/deliveries/{delivery_id}/replay")
def replay(delivery_id: str, db: Session = Depends(get_db), p: Principal = Depends(DEV)):
    d = db.get(WebhookDelivery, parse_uuid(delivery_id, "Delivery"))
    if d is None or d.store_id != p.store.id:
        raise NotFound("Delivery")
    ep = db.get(WebhookEndpoint, d.endpoint_id)
    new_id = enqueue(db, ep, d.event_type, d.payload, source="replay", replay_of=d.id)
    nd = deliver(db, db.get(WebhookDelivery, new_id))
    audit(db, p.store.id, p.actor, "webhooks.replayed", "webhook_delivery", d.id, {"new": str(new_id)})
    db.commit()
    return d_out(nd, full=True)
