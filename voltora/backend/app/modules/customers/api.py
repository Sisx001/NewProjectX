"""Customers admin API: list/search, profile, notes/tags/consent, PII-gated export and erasure."""
from __future__ import annotations

import importlib
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, require
from ...core.errors import AppError, NotFound
from ...core.services import Page, audit, emit
from ...db import get_db, utcnow
from .models import Address, Customer
from .service import EV_CUSTOMER_ERASED, customer_filter, mask_email, mask_phone

router = APIRouter(prefix="/api/customers", tags=["customers"])
MODULE = {"title": "Customers", "description": "Customer profiles, consent, privacy export and erasure"}
ERASED_NAME = "Erased customer"


def _out(c: Customer, pii: bool, currency: str) -> dict:
    return {"id": str(c.id), "name": c.name, "email": c.email if pii else mask_email(c.email),
            "phone": c.phone if pii else mask_phone(c.phone), "tags": c.tags or [], "note": c.note,
            "marketing_consent": c.marketing_consent,
            "consent_updated_at": c.consent_updated_at.isoformat() if c.consent_updated_at else None,
            "orders_count": c.orders_count, "total_spent_minor": c.total_spent_minor, "currency": currency,
            "last_order_at": c.last_order_at.isoformat() if c.last_order_at else None, "has_account": c.user_id is not None,
            "is_test": c.is_test, "erased": c.erased_at is not None, "pii_masked": not pii,
            "created_at": c.created_at.isoformat()}


def _get(db: Session, p: Principal, cid: str) -> Customer:
    c = db.execute(select(Customer).where(Customer.id == parse_uuid(cid, "Customer"),
                                          Customer.store_id == p.store.id)).scalar_one_or_none()
    if c is None:
        raise NotFound("Customer")
    return c


@router.get("")
def list_customers(
    q: str = Query("", max_length=100), tag: str | None = Query(None, max_length=40),
    consent: str | None = Query(None, pattern="^(yes|no)$"), has_orders: str | None = Query(None, pattern="^(yes|no)$"),
    sort: str = Query("recent", pattern="^(recent|name|spent|orders)$"), page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db), p: Principal = Depends(require("customers.read")),
):
    pii = p.can("customers.pii")
    base = select(Customer).where(Customer.store_id == p.store.id)
    if q.strip():
        base = base.where(customer_filter(q, pii))
    if tag:
        base = base.where(Customer.tags.contains([tag]))
    if consent:
        base = base.where(Customer.marketing_consent.is_(consent == "yes"))
    if has_orders:
        base = base.where(Customer.orders_count > 0 if has_orders == "yes" else Customer.orders_count == 0)
    order = {"recent": Customer.created_at.desc(), "name": func.lower(Customer.name), "spent": Customer.total_spent_minor.desc(),
             "orders": Customer.orders_count.desc()}[sort]
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(order, Customer.id).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return Page([_out(c, pii, p.store.currency) for c in rows], total, page, page_size,
                {"pii_masked": not pii}).dump()


@router.get("/tags")
def tags(db: Session = Depends(get_db), p: Principal = Depends(require("customers.read"))):
    rows = db.execute(select(func.jsonb_array_elements_text(Customer.tags).label("t"))
                      .where(Customer.store_id == p.store.id).distinct()).all()
    return {"items": sorted({r[0] for r in rows})}


@router.get("/{customer_id}")
def get_customer(customer_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("customers.read"))):
    c = _get(db, p, customer_id)
    pii = p.can("customers.pii")
    addrs = db.execute(select(Address).where(Address.customer_id == c.id).order_by(Address.is_default.desc(),
                                                                                   Address.created_at)).scalars().all()
    out = _out(c, pii, p.store.currency)
    out["addresses"] = [{
        "id": str(a.id), "label": a.label, "name": a.name if pii else (a.name[:1] + "•••" if a.name else ""),
        "phone": a.phone if pii else mask_phone(a.phone), "line1": a.line1 if pii else "•••",
        "line2": a.line2 if pii else "", "area": a.area, "upazila": a.upazila, "district": a.district,
        "division": a.division, "postal_code": a.postal_code if pii else "", "is_default": a.is_default} for a in addrs]
    out["orders_link"] = f"/admin/orders?customer={c.id}"
    return out


class CustomerPatch(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    tags: list[str] | None = None
    note: str | None = Field(default=None, max_length=5000)
    marketing_consent: bool | None = None


@router.patch("/{customer_id}")
def update_customer(customer_id: str, body: CustomerPatch, db: Session = Depends(get_db),
                    p: Principal = Depends(require("customers.write"))):
    c = _get(db, p, customer_id)
    if c.erased_at:
        raise AppError("customer_erased", "This customer's data has been erased and cannot be edited")
    f = body.model_fields_set
    changed = []
    if "name" in f and body.name is not None:
        c.name = body.name.strip()
        changed.append("name")
    if "tags" in f and body.tags is not None:
        clean: list[str] = []
        for t in body.tags:
            t = " ".join(t.split())[:40]
            if t and t.lower() not in {x.lower() for x in clean}:
                clean.append(t)
        c.tags = clean[:30]
        changed.append("tags")
    if "note" in f and body.note is not None:
        c.note = body.note
        changed.append("note")
    if "marketing_consent" in f and body.marketing_consent is not None and body.marketing_consent != c.marketing_consent:
        c.marketing_consent = body.marketing_consent
        c.consent_updated_at = utcnow()
        changed.append("marketing_consent")
    if changed:
        audit(db, p.store.id, p.actor, "customer.updated", "customer", c.id, {"fields": changed})
    out = _out(c, p.can("customers.pii"), p.store.currency)
    db.commit()
    return out


def _orders_snapshot(db: Session, c: Customer) -> list[dict] | None:
    """Best effort: the orders module is owned elsewhere and may not be installed."""
    try:
        mod = importlib.import_module("app.modules.orders.models")
        order = mod.Order
        cols = [getattr(order, k) for k in ("number", "status", "payment_status", "total_minor", "currency", "created_at")
                if hasattr(order, k)]
        if not hasattr(order, "customer_id") or not cols:
            return None
        return [{k.key: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(cols, row, strict=True)}
                for row in db.execute(select(*cols).where(order.customer_id == c.id)).all()]
    except (ImportError, AttributeError):
        return None


@router.post("/{customer_id}/export")
def export_customer(customer_id: str, db: Session = Depends(get_db),
                    p: Principal = Depends(require("customers.read", "customers.pii"))):
    c = _get(db, p, customer_id)
    addrs = db.execute(select(Address).where(Address.customer_id == c.id)).scalars().all()
    data = {
        "exported_at": utcnow().isoformat(), "store": p.store.name,
        "customer": {"id": str(c.id), "name": c.name, "email": c.email, "phone": c.phone, "tags": c.tags or [],
                     "note": c.note, "marketing_consent": c.marketing_consent, "locale": c.locale,
                     "consent_updated_at": c.consent_updated_at.isoformat() if c.consent_updated_at else None,
                     "orders_count": c.orders_count, "total_spent_minor": c.total_spent_minor,
                     "created_at": c.created_at.isoformat(), "erased_at": c.erased_at.isoformat() if c.erased_at else None},
        "addresses": [{"label": a.label, "name": a.name, "phone": a.phone, "line1": a.line1, "line2": a.line2,
                       "area": a.area, "upazila": a.upazila, "district": a.district, "division": a.division,
                       "postal_code": a.postal_code} for a in addrs],
        "orders": _orders_snapshot(db, c),
    }
    audit(db, p.store.id, p.actor, "customer.exported", "customer", c.id, {})
    db.commit()
    return data


class EraseIn(BaseModel):
    confirm: bool = False


@router.post("/{customer_id}/erase")
def erase_customer(customer_id: str, body: EraseIn, db: Session = Depends(get_db),
                   p: Principal = Depends(require("customers.write", "customers.pii"))):
    if not body.confirm:
        raise AppError("confirmation_required", "Erasure is permanent. Send confirm=true to proceed.")
    c = _get(db, p, customer_id)
    if c.erased_at:
        return _out(c, True, p.store.currency)
    from ..identity.models import AuthSession, User

    c.email, c.phone, c.name, c.note, c.tags = None, None, ERASED_NAME, "", []
    c.marketing_consent, c.consent_updated_at, c.erased_at = False, utcnow(), utcnow()
    db.query(Address).filter(Address.customer_id == c.id).delete()
    if c.user_id:
        u = db.get(User, c.user_id)
        if u is not None and u.kind == "customer":
            u.email, u.name, u.phone = f"erased-{uuid.uuid4().hex[:12]}@erased.invalid", ERASED_NAME, None
            u.password_hash, u.status = None, "disabled"
            db.execute(update(AuthSession).where(AuthSession.user_id == u.id, AuthSession.revoked_at.is_(None))
                       .values(revoked_at=utcnow()))
    audit(db, p.store.id, p.actor, "customer.erased", "customer", c.id,
          {"orders_retained": c.orders_count, "note": "PII anonymised; order financial records kept"})
    emit(db, p.store.id, EV_CUSTOMER_ERASED, {"customer_id": str(c.id)})
    out = _out(c, True, p.store.currency)
    db.commit()
    return out

