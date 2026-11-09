from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, require
from ...core.errors import AppError, Conflict, NotFound
from ...core.services import audit
from ...db import get_db
from .models import Discount, DiscountRedemption
from .service import normalize_code

router = APIRouter(prefix="/api", tags=["discounts"])
MODULE = {"title": "Discounts", "description": "Promo codes with atomic usage limits and per-customer caps"}


class DiscountIn(BaseModel):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(default="", max_length=500)
    kind: str = Field(pattern="^(percentage|fixed|free_shipping)$")
    percent: int | None = Field(default=None, ge=1, le=100)
    amount_minor: int | None = Field(default=None, ge=1)
    max_discount_minor: int | None = Field(default=None, ge=1)
    min_subtotal_minor: int = Field(default=0, ge=0)
    applies_to: str = Field(default="all", pattern="^(all|products|categories)$")
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    usage_limit: int | None = Field(default=None, ge=1)
    per_customer_limit: int | None = Field(default=None, ge=1)
    active: bool = True

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "percentage" and not self.percent:
            raise ValueError("percentage discounts need percent (1–100)")
        if self.kind == "fixed" and not self.amount_minor:
            raise ValueError("fixed discounts need amount_minor")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.applies_to == "products" and not self.product_ids:
            raise ValueError("choose at least one product")
        if self.applies_to == "categories" and not self.category_ids:
            raise ValueError("choose at least one category")
        return self


def _out(d: Discount, redemptions: int | None = None) -> dict:
    return {"id": str(d.id), "code": d.code, "description": d.description, "kind": d.kind, "percent": d.percent,
            "amount_minor": d.amount_minor, "max_discount_minor": d.max_discount_minor,
            "min_subtotal_minor": d.min_subtotal_minor, "applies_to": d.applies_to,
            "product_ids": [str(x) for x in d.product_ids or []], "category_ids": [str(x) for x in d.category_ids or []],
            "starts_at": d.starts_at.isoformat() if d.starts_at else None,
            "ends_at": d.ends_at.isoformat() if d.ends_at else None, "usage_limit": d.usage_limit,
            "per_customer_limit": d.per_customer_limit, "used_count": d.used_count, "active": d.active,
            "created_at": d.created_at.isoformat(), "redeemed_minor": redemptions}


def _apply(d: Discount, body: DiscountIn) -> None:
    data = body.model_dump()
    data["code"] = normalize_code(body.code)
    data["product_ids"] = [str(x) for x in body.product_ids]
    data["category_ids"] = [str(x) for x in body.category_ids]
    if body.kind != "percentage":
        data["percent"] = None
        data["max_discount_minor"] = None
    if body.kind != "fixed":
        data["amount_minor"] = None
    for k, v in data.items():
        setattr(d, k, v)


@router.get("/admin/discounts")
def list_discounts(q: str | None = Query(None, max_length=40), status: str | None = None,
                   db: Session = Depends(get_db), p: Principal = Depends(require("marketing.read"))):
    conds = [Discount.store_id == p.store.id]
    if q:
        conds.append(Discount.code.ilike(f"%{q.strip().upper()}%"))
    if status == "active":
        conds.append(Discount.active.is_(True))
    elif status == "inactive":
        conds.append(Discount.active.is_(False))
    rows = list(db.execute(select(Discount).where(*conds).order_by(Discount.created_at.desc())).scalars())
    sums = dict(db.execute(select(DiscountRedemption.discount_id, func.sum(DiscountRedemption.amount_minor))
                           .where(DiscountRedemption.discount_id.in_([d.id for d in rows]),
                                  DiscountRedemption.status == "active")
                           .group_by(DiscountRedemption.discount_id)).all()) if rows else {}
    return {"items": [_out(d, int(sums.get(d.id, 0))) for d in rows]}


@router.post("/admin/discounts")
def create_discount(body: DiscountIn, db: Session = Depends(get_db),
                    p: Principal = Depends(require("marketing.write"))):
    if db.execute(select(Discount.id).where(Discount.store_id == p.store.id,
                                            Discount.code == normalize_code(body.code))).first():
        raise Conflict("code_taken", "A discount with this code already exists")
    d = Discount(store_id=p.store.id, created_by=p.user.id if p.user else None)
    _apply(d, body)
    db.add(d)
    db.flush()
    audit(db, p.store.id, p.actor, "marketing.discount_created", "discount", d.id, {"code": d.code, "kind": d.kind})
    db.commit()
    return _out(d)


def _get(db: Session, p: Principal, discount_id: str) -> Discount:
    d = db.get(Discount, parse_uuid(discount_id, "Discount"))
    if d is None or d.store_id != p.store.id:
        raise NotFound("Discount")
    return d


@router.get("/admin/discounts/{discount_id}")
def get_discount(discount_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("marketing.read"))):
    from ..commerce.models import Order

    d = _get(db, p, discount_id)
    reds = db.execute(select(DiscountRedemption, Order.number).join(Order, Order.id == DiscountRedemption.order_id)
                      .where(DiscountRedemption.discount_id == d.id)
                      .order_by(DiscountRedemption.created_at.desc()).limit(100)).all()
    return {**_out(d), "redemptions": [{"order_number": n, "amount_minor": r.amount_minor, "status": r.status,
                                        "created_at": r.created_at.isoformat()} for r, n in reds]}


@router.put("/admin/discounts/{discount_id}")
def update_discount(discount_id: str, body: DiscountIn, db: Session = Depends(get_db),
                    p: Principal = Depends(require("marketing.write"))):
    d = db.execute(select(Discount).where(Discount.id == parse_uuid(discount_id, "Discount"),
                                          Discount.store_id == p.store.id).with_for_update()).scalar_one_or_none()
    if d is None:
        raise NotFound("Discount")
    code = normalize_code(body.code)
    if code != d.code and db.execute(select(Discount.id).where(Discount.store_id == p.store.id,
                                                               Discount.code == code)).first():
        raise Conflict("code_taken", "A discount with this code already exists")
    if body.usage_limit is not None and body.usage_limit < d.used_count:
        raise AppError("limit_below_usage", f"This code was already used {d.used_count} times")
    _apply(d, body)
    audit(db, p.store.id, p.actor, "marketing.discount_updated", "discount", d.id, {"code": d.code})
    db.commit()
    return _out(d)


@router.delete("/admin/discounts/{discount_id}")
def delete_discount(discount_id: str, db: Session = Depends(get_db),
                    p: Principal = Depends(require("marketing.write"))):
    d = _get(db, p, discount_id)
    if d.used_count > 0 or db.execute(select(DiscountRedemption.id).where(
            DiscountRedemption.discount_id == d.id)).first():
        d.active = False  # keep history for orders that used it
        audit(db, p.store.id, p.actor, "marketing.discount_deactivated", "discount", d.id, {"code": d.code})
        db.commit()
        return {"ok": True, "deactivated": True}
    audit(db, p.store.id, p.actor, "marketing.discount_deleted", "discount", d.id, {"code": d.code})
    db.delete(d)
    db.commit()
    return {"ok": True, "deleted": True}
