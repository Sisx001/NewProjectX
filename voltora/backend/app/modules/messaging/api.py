from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, require
from ...core.errors import NotFound
from ...core.security import mask, seal
from ...core.services import audit
from ...db import get_db, utcnow
from .models import MessageTemplate, OutboundMessage
from .service import DEFAULT_TEMPLATES, SMTP_PASSWORD_REF, deliver, queue_message, render

router = APIRouter(prefix="/api/messaging", tags=["messaging"])


def msg_out(m: OutboundMessage) -> dict:
    return {"id": str(m.id), "channel": m.channel, "to": m.to, "template": m.template, "subject": m.subject,
            "body": "[hidden: contains a private link]" if m.sensitive else m.body, "status": m.status,
            "attempts": m.attempts, "error": m.error, "created_at": m.created_at.isoformat(),
            "sent_at": m.sent_at.isoformat() if m.sent_at else None}


@router.get("/messages")
def list_messages(status: str | None = None, page: int = Query(1, ge=1), db: Session = Depends(get_db),
                  p: Principal = Depends(require("system.read"))):
    q = select(OutboundMessage).where(OutboundMessage.store_id == p.store.id)
    if status:
        q = q.where(OutboundMessage.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(OutboundMessage.created_at.desc()).offset((page - 1) * 50).limit(50)).scalars()
    return {"items": [msg_out(m) for m in rows], "total": total, "page": page, "page_size": 50}


@router.post("/messages/{message_id}/retry")
def retry(message_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("system.manage"))):
    m = db.get(OutboundMessage, parse_uuid(message_id))
    if m is None or m.store_id != p.store.id:
        raise NotFound("Message")
    deliver(db, m)
    audit(db, p.store.id, p.actor, "messaging.retry", "message", m.id, {"status": m.status})
    db.commit()
    return msg_out(m)


class TestIn(BaseModel):
    to: str = Field(min_length=3, max_length=254)


@router.post("/test-email")
def test_email(body: TestIn, db: Session = Depends(get_db), p: Principal = Depends(require("settings.write"))):
    m = queue_message(db, p.store.id, channel="email", to=body.to, subject="VOLTORA test email",
                      body="This is a test message from your VOLTORA store settings.")
    db.flush()
    deliver(db, m)
    db.commit()
    return msg_out(m)


class SmtpSecretIn(BaseModel):
    password: str = Field(min_length=1, max_length=500)


@router.put("/smtp-password")
def set_smtp_password(body: SmtpSecretIn, db: Session = Depends(get_db),
                      p: Principal = Depends(require("settings.write"))):
    from ..platform.models import SecretRecord

    rec = db.execute(select(SecretRecord).where(SecretRecord.store_id == p.store.id,
                                                SecretRecord.ref == SMTP_PASSWORD_REF)).scalar_one_or_none()
    if rec is None:
        db.add(SecretRecord(store_id=p.store.id, ref=SMTP_PASSWORD_REF, sealed=seal(body.password),
                            hint=mask(body.password)))
    else:
        rec.sealed, rec.hint, rec.rotated_at = seal(body.password), mask(body.password), utcnow()
    audit(db, p.store.id, p.actor, "secrets.updated", "secret", SMTP_PASSWORD_REF)
    db.commit()
    return {"ok": True, "hint": mask(body.password)}


@router.get("/templates")
def list_templates(db: Session = Depends(get_db), p: Principal = Depends(require("settings.read"))):
    overrides = {(t.key, t.locale): t for t in db.execute(
        select(MessageTemplate).where(MessageTemplate.store_id == p.store.id)).scalars()}
    out = []
    for key, base in DEFAULT_TEMPLATES.items():
        t = overrides.get((key, "en"))
        out.append({"key": key, "locale": "en", "subject": t.subject if t else base["subject"],
                    "body": t.body if t else base["body"], "customized": t is not None})
    return {"items": out}


class TemplateIn(BaseModel):
    subject: str = Field(max_length=300)
    body: str = Field(max_length=20000)
    locale: str = "en"


@router.put("/templates/{key}")
def save_template(key: str, body: TemplateIn, db: Session = Depends(get_db),
                  p: Principal = Depends(require("settings.write"))):
    if key not in DEFAULT_TEMPLATES:
        raise NotFound("Template")
    t = db.execute(select(MessageTemplate).where(MessageTemplate.store_id == p.store.id, MessageTemplate.key == key,
                                                 MessageTemplate.locale == body.locale)).scalar_one_or_none()
    if t is None:
        t = MessageTemplate(store_id=p.store.id, key=key, locale=body.locale)
        db.add(t)
    t.subject, t.body = body.subject, body.body
    audit(db, p.store.id, p.actor, "messaging.template_saved", "template", key)
    db.commit()
    subject, rendered = render(db, p.store.id, key, {"name": "Rahim", "order_number": "VT-100001", "total": "৳1,250",
                                                     "link": "https://example.com", "status": "shipped"})
    return {"ok": True, "preview": {"subject": subject, "body": rendered}}
