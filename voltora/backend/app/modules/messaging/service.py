"""Transactional messaging: templates, queue and transports (SMTP; SMS via plugins)."""
from __future__ import annotations

import datetime as dt
import smtplib
import string
import uuid
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.security import unseal
from ...core.services import get_setting, periodic, setting
from ...db import utcnow
from .models import MessageTemplate, OutboundMessage

SMTP_HOST = setting(key="email.smtp_host", type="string", default="", label="SMTP host", group="Email",
                    description="Leave empty to hold outgoing email (messages are kept, never marked sent).")
SMTP_PORT = setting(key="email.smtp_port", type="int", default=587, label="SMTP port", group="Email", min=1, max=65535)
SMTP_USER = setting(key="email.smtp_username", type="string", default="", label="SMTP username", group="Email")
SMTP_TLS = setting(key="email.smtp_security", type="enum", default="starttls", choices=["starttls", "ssl", "none"],
                   label="SMTP security", group="Email")
FROM_EMAIL = setting(key="email.from_address", type="email", default="", label="From address", group="Email")
FROM_NAME = setting(key="email.from_name", type="string", default="", label="From name", group="Email", max=120)
SMTP_PASSWORD_REF = "email.smtp_password"

DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "auth.password_reset": {"subject": "Reset your password",
                            "body": "Hi $name,\n\nUse this link within 2 hours to choose a new password:\n$link\n\n"
                                    "If you did not ask for this, you can ignore this email."},
    "staff.invite": {"subject": "You're invited to $store",
                     "body": "Hi $name,\n\nYou have been invited to $store as $role.\nAccept: $link\n\n"
                             "This link expires in 72 hours."},
    "order.placed": {"subject": "Order $order_number received",
                     "body": "Hi $name,\n\nThanks for your order $order_number ($total).\n"
                             "Payment: $payment_method\nTrack it any time: $link"},
    "order.status": {"subject": "Order $order_number: $status",
                     "body": "Hi $name,\n\nYour order $order_number is now $status.\n$link"},
    "order.refunded": {"subject": "Refund for order $order_number",
                       "body": "Hi $name,\n\nWe refunded $amount for order $order_number. "
                               "Depending on the payment method it may take a few days to appear."},
    "support.reply": {"subject": "Re: $subject", "body": "$body"},
}


def render(db: Session, store_id: uuid.UUID, key: str, context: dict, locale: str = "en") -> tuple[str, str]:
    t = db.execute(select(MessageTemplate).where(MessageTemplate.store_id == store_id, MessageTemplate.key == key,
                                                 MessageTemplate.locale == locale, MessageTemplate.enabled)
                   ).scalar_one_or_none()
    base = DEFAULT_TEMPLATES.get(key, {"subject": key, "body": ""})
    subject = t.subject if t else base["subject"]
    body = t.body if t else base["body"]
    ctx = {k: str(v) for k, v in context.items()}
    return string.Template(subject).safe_substitute(ctx), string.Template(body).safe_substitute(ctx)


def queue_message(db: Session, store_id: uuid.UUID, *, channel: str, to: str, template: str | None = None,
                  context: dict | None = None, subject: str = "", body: str = "", sensitive: bool = False,
                  related: dict | None = None) -> OutboundMessage:
    if template:
        subject, body = render(db, store_id, template, context or {})
    m = OutboundMessage(store_id=store_id, channel=channel, to=to, template=template, subject=subject, body=body,
                        sensitive=sensitive, related=related or {})
    db.add(m)
    return m


class TransportUnavailable(Exception):
    pass


def smtp_send(db: Session, m: OutboundMessage) -> str:
    from ..platform.models import SecretRecord

    host = get_setting(db, m.store_id, SMTP_HOST.key)
    sender = get_setting(db, m.store_id, FROM_EMAIL.key)
    if not host or not sender:
        raise TransportUnavailable("Email transport is not configured (Settings → Email)")
    port = get_setting(db, m.store_id, SMTP_PORT.key)
    mode = get_setting(db, m.store_id, SMTP_TLS.key)
    user = get_setting(db, m.store_id, SMTP_USER.key)
    secret = db.execute(select(SecretRecord).where(SecretRecord.store_id == m.store_id,
                                                   SecretRecord.ref == SMTP_PASSWORD_REF)).scalar_one_or_none()
    msg = EmailMessage()
    name = get_setting(db, m.store_id, FROM_NAME.key)
    msg["From"] = f"{name} <{sender}>" if name else sender
    msg["To"] = m.to
    msg["Subject"] = m.subject
    msg["Message-ID"] = f"<{m.id}@voltora>"
    msg.set_content(m.body)
    cls = smtplib.SMTP_SSL if mode == "ssl" else smtplib.SMTP
    with cls(host, port, timeout=20) as s:
        if mode == "starttls":
            s.starttls()
        if user and secret:
            s.login(user, unseal(secret.sealed))
        s.send_message(msg)
    return msg["Message-ID"]


TRANSPORTS = {"email": smtp_send}  # plugins register sms/whatsapp transports here


def deliver(db: Session, m: OutboundMessage) -> None:
    fn = TRANSPORTS.get(m.channel)
    m.attempts += 1
    try:
        if fn is None:
            raise TransportUnavailable(f"No {m.channel} transport is installed")
        m.provider_message_id = fn(db, m)
        m.status, m.sent_at, m.error, m.transport = "sent", utcnow(), None, m.channel
    except TransportUnavailable as exc:
        m.status, m.error = "held", str(exc)
        m.next_attempt_at = utcnow() + dt.timedelta(minutes=10)
    except Exception as exc:  # noqa: BLE001 - transport failures are recorded and retried
        m.error = str(exc)[:1000]
        m.status = "failed" if m.attempts >= 5 else "queued"
        m.next_attempt_at = utcnow() + dt.timedelta(seconds=30 * 2 ** m.attempts)


@periodic("messaging.deliver", every_seconds=5)
def deliver_pending(db: Session):
    rows = db.execute(select(OutboundMessage).where(OutboundMessage.status.in_(["queued", "held"]),
                                                    OutboundMessage.next_attempt_at <= utcnow())
                      .limit(25).with_for_update(skip_locked=True)).scalars().all()
    for m in rows:
        deliver(db, m)
    return {"processed": len(rows)}
