"""Customer helpers: PII masking, search hook, export and erasure."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...core.deps import Principal
from ...core.services import SEARCHERS, event_type
from .models import Customer

EV_CUSTOMER_ERASED = event_type("customer.erased", "A customer's personal data was anonymised on request")


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    local, _, domain = email.partition("@")
    return f"{local[:1]}•••@{domain.split('.')[0][:1]}•••.{domain.rsplit('.', 1)[-1]}" if "." in domain else f"{local[:1]}•••@•••"


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return phone
    digits = [c for c in phone if c.isdigit()]
    return "•••••••••" + "".join(digits[-2:]) if len(digits) > 2 else "•••"


def customer_filter(q: str, can_pii: bool):
    """Substring search on name; email/phone only match exactly unless the viewer holds customers.pii."""
    q = q.strip()
    pat = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if can_pii:
        return or_(Customer.name.ilike(pat), Customer.email.ilike(pat), Customer.phone.ilike(pat))
    return or_(Customer.name.ilike(pat), Customer.email == q.lower(), Customer.phone == q)


def _search_customers(db: Session, p: Principal, q: str) -> list[dict]:
    if not p.can("customers.read"):
        return []
    pii = p.can("customers.pii")
    rows = db.execute(select(Customer).where(Customer.store_id == p.store.id, Customer.erased_at.is_(None),
                                             customer_filter(q, pii)).order_by(Customer.updated_at.desc()).limit(6)
                      ).scalars().all()
    return [{"label": c.name or (c.email if pii else mask_email(c.email)) or "Customer",
             "sublabel": "Customer · " + ((c.email or c.phone or "") if pii else (mask_email(c.email) or mask_phone(c.phone) or "")),
             "path": f"/admin/customers/{c.id}", "kind": "customer"} for c in rows]


SEARCHERS.append(_search_customers)
