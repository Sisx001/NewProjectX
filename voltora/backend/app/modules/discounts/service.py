"""Discount evaluation and atomic redemption. Usage is returned when an order is cancelled or expires."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ...core.errors import AppError
from ...db import utcnow
from .models import Discount, DiscountRedemption


class DiscountInvalid(AppError):
    status = 400

    def __init__(self, message: str):
        super().__init__("discount_invalid", message)


@dataclass
class DiscountResult:
    discount: Discount
    amount_minor: int
    free_shipping: bool
    eligible_minor: int


def normalize_code(code: str) -> str:
    return (code or "").strip().upper()


def find(db: Session, store_id: uuid.UUID, code: str) -> Discount | None:
    return db.execute(select(Discount).where(Discount.store_id == store_id, Discount.code == normalize_code(code))
                      ).scalar_one_or_none()


def _money(minor: int) -> str:
    return f"৳{minor / 100:,.2f}".replace(".00", "")


def evaluate(db: Session, store_id: uuid.UUID, code: str, lines: list[dict], subtotal_minor: int,
             customer_key: str | None = None) -> DiscountResult:
    """lines: [{product_id, category_ids: set[str], total_minor}] — server-computed line totals."""
    d = find(db, store_id, code)
    now = utcnow()
    if d is None or not d.active:
        raise DiscountInvalid("This code isn't valid")
    if d.starts_at and d.starts_at > now:
        raise DiscountInvalid("This code isn't active yet")
    if d.ends_at and d.ends_at <= now:
        raise DiscountInvalid("This code has expired")
    if d.usage_limit is not None and d.used_count >= d.usage_limit:
        raise DiscountInvalid("This code has been fully redeemed")
    if customer_key and d.per_customer_limit:
        used = db.execute(select(func.count()).select_from(DiscountRedemption).where(
            DiscountRedemption.discount_id == d.id, DiscountRedemption.customer_key == customer_key,
            DiscountRedemption.status == "active")).scalar_one()
        if used >= d.per_customer_limit:
            raise DiscountInvalid("You've already used this code")
    if d.applies_to == "products":
        wanted = {str(x) for x in d.product_ids or []}
        eligible = sum(ln["total_minor"] for ln in lines if str(ln["product_id"]) in wanted)
    elif d.applies_to == "categories":
        wanted = {str(x) for x in d.category_ids or []}
        eligible = sum(ln["total_minor"] for ln in lines if wanted & {str(c) for c in ln.get("category_ids", ())})
    else:
        eligible = subtotal_minor
    if eligible <= 0:
        raise DiscountInvalid("This code doesn't apply to the items in your cart")
    # Minimum spend is measured on the same base the discount applies to (legacy compared pre-tier subtotal).
    if d.min_subtotal_minor and eligible < d.min_subtotal_minor:
        raise DiscountInvalid(f"Add {_money(d.min_subtotal_minor - eligible)} more of eligible items to use this code")
    if d.kind == "percentage":
        amount = (eligible * int(d.percent or 0) + 50) // 100
        if d.max_discount_minor:
            amount = min(amount, d.max_discount_minor)
    elif d.kind == "fixed":
        amount = int(d.amount_minor or 0)
    else:
        amount = 0
    amount = max(0, min(amount, eligible, subtotal_minor))
    return DiscountResult(d, amount, d.kind == "free_shipping", eligible)


def redeem(db: Session, result: DiscountResult, order_id: uuid.UUID, customer_key: str) -> DiscountRedemption:
    """Atomically consume one use (caller's transaction). Raises DiscountInvalid when the limit was reached."""
    d = result.discount
    if d.per_customer_limit:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"discount:{d.id}:{customer_key}"})
        used = db.execute(select(func.count()).select_from(DiscountRedemption).where(
            DiscountRedemption.discount_id == d.id, DiscountRedemption.customer_key == customer_key,
            DiscountRedemption.status == "active")).scalar_one()
        if used >= d.per_customer_limit:
            raise DiscountInvalid("You've already used this code")
    ok = db.execute(
        update(Discount).where(Discount.id == d.id, Discount.active.is_(True),
                               (Discount.usage_limit.is_(None)) | (Discount.used_count < Discount.usage_limit))
        .values(used_count=Discount.used_count + 1).returning(Discount.id)
    ).first()
    if ok is None:
        raise DiscountInvalid("This code has been fully redeemed")
    r = DiscountRedemption(discount_id=d.id, order_id=order_id, customer_key=customer_key,
                           amount_minor=result.amount_minor)
    db.add(r)
    return r


def release_for_order(db: Session, order_id: uuid.UUID, note: str) -> bool:
    """Return the usage consumed by an order (cancel/expiry). Idempotent."""
    r = db.execute(select(DiscountRedemption).where(DiscountRedemption.order_id == order_id,
                                                    DiscountRedemption.status == "active").with_for_update()
                   ).scalar_one_or_none()
    if r is None:
        return False
    r.status, r.released_at, r.note = "released", utcnow(), note
    db.execute(update(Discount).where(Discount.id == r.discount_id, Discount.used_count > 0)
               .values(used_count=Discount.used_count - 1))
    return True
