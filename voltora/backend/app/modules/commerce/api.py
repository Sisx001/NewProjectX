"""Storefront cart/checkout/order APIs, customer account APIs, admin orders and the admin home."""
from __future__ import annotations

import datetime as dt
import re
import uuid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ...core.deps import Principal, customer, parse_uuid, principal, require, staff
from ...core.errors import AppError, Forbidden, NotFound
from ...core.services import NAV_COUNTERS, SEARCHERS, audit, get_setting, limiter
from ...db import get_db, utcnow
from .models import Order, OrderEvent, OrderLine
from .orders import (
    ORDER_STATUSES,
    ORDER_TRANSITIONS,
    STATUS_LABELS,
    cancel_order,
    check_access_token,
    get_order,
    lines_of,
    timeline,
    transition,
)
from .phone import InvalidPhone, mask_phone, normalize_bd_mobile
from .service import (
    add_line,
    build_quote,
    cart_payload,
    check_stock,
    place_order,
    resolve_cart,
)

router = APIRouter(prefix="/api", tags=["commerce"])
MODULE = {"title": "Orders & checkout",
          "description": "Cart, server-priced checkout, orders, state machine, customer accounts, admin home"}


# ================================================================ storefront: locations
@router.get("/store/locations")
def store_locations(response: Response):
    from ..shipping.locations import tree

    response.headers["Cache-Control"] = "public, max-age=86400"
    return {"divisions": tree()["divisions"]}


# ================================================================ storefront: cart
class LineIn(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(ge=1, le=100)


class QtyIn(BaseModel):
    quantity: int = Field(ge=0, le=100)


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


@router.get("/store/cart")
def get_cart(request: Request, response: Response, db: Session = Depends(get_db), p: Principal = Depends(principal)):
    cart = resolve_cart(db, request, response, p, create=False)
    out = cart_payload(db, p.store, cart)
    db.commit()
    return out


@router.post("/store/cart/lines")
def add_cart_line(body: LineIn, request: Request, response: Response, db: Session = Depends(get_db),
                  p: Principal = Depends(principal)):
    limiter.hit(f"cart:{p.ip}", 120, 60)
    cart = resolve_cart(db, request, response, p, create=True)
    add_line(db, cart, body.variant_id, body.quantity, p.store.id)
    db.commit()
    return cart_payload(db, p.store, cart)


def _cart_line(db: Session, cart, line_id: str):
    from .models import CartLine

    ln = db.get(CartLine, parse_uuid(line_id, "Cart line")) if cart else None
    if ln is None or ln.cart_id != cart.id:
        raise NotFound("Cart line")
    return ln


@router.patch("/store/cart/lines/{line_id}")
def update_cart_line(line_id: str, body: QtyIn, request: Request, response: Response, db: Session = Depends(get_db),
                     p: Principal = Depends(principal)):
    cart = resolve_cart(db, request, response, p, create=False)
    ln = _cart_line(db, cart, line_id)
    if body.quantity == 0:
        db.delete(ln)
    else:
        if body.quantity > ln.quantity:
            check_stock(db, p.store.id, ln.variant_id, body.quantity)
        ln.quantity = body.quantity
    db.commit()
    return cart_payload(db, p.store, cart)


@router.delete("/store/cart/lines/{line_id}")
def delete_cart_line(line_id: str, request: Request, response: Response, db: Session = Depends(get_db),
                     p: Principal = Depends(principal)):
    cart = resolve_cart(db, request, response, p, create=False)
    db.delete(_cart_line(db, cart, line_id))
    db.commit()
    return cart_payload(db, p.store, cart)


@router.post("/store/cart/discount")
def apply_cart_discount(body: CodeIn, request: Request, response: Response, db: Session = Depends(get_db),
                        p: Principal = Depends(principal)):
    from ..discounts.service import DiscountInvalid, evaluate, normalize_code
    from .service import cart_items, price_items

    limiter.hit(f"discount:{p.ip}", 20, 60)
    cart = resolve_cart(db, request, response, p, create=True)
    priced = [pl for pl in price_items(db, p.store.id, cart_items(db, cart)) if pl.sellable]
    if not priced:
        raise DiscountInvalid("Add items to your cart before applying a code")
    evaluate(db, p.store.id, body.code, [{"product_id": pl.product.id, "category_ids": pl.category_ids,
                                          "total_minor": pl.line_total_minor} for pl in priced],
             sum(pl.line_total_minor for pl in priced))
    cart.discount_code = normalize_code(body.code)
    db.commit()
    return cart_payload(db, p.store, cart)


@router.delete("/store/cart/discount")
def remove_cart_discount(request: Request, response: Response, db: Session = Depends(get_db),
                         p: Principal = Depends(principal)):
    cart = resolve_cart(db, request, response, p, create=False)
    if cart:
        cart.discount_code = None
    db.commit()
    return cart_payload(db, p.store, cart)


# ================================================================ storefront: checkout
class QuoteAddress(BaseModel):
    district: str = Field(min_length=1, max_length=120)
    upazila: str | None = Field(default=None, max_length=120)


class QuoteIn(BaseModel):
    address: QuoteAddress
    payment_method: str | None = None
    shipping_option_id: str | None = None


class AddressIn(BaseModel):
    line1: str = Field(min_length=1, max_length=300)
    line2: str | None = Field(default="", max_length=300)
    area: str | None = Field(default="", max_length=120)
    upazila: str | None = Field(default="", max_length=120)
    district: str = Field(min_length=1, max_length=120)
    division: str | None = Field(default="", max_length=120)
    postal_code: str | None = Field(default="", max_length=12)


class CheckoutIn(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_\-:.]+$")
    email: str | None = Field(default=None, max_length=254)
    phone: str = Field(min_length=6, max_length=32)
    name: str = Field(min_length=1, max_length=160)
    address: AddressIn
    shipping_option_id: str = Field(min_length=1, max_length=64)
    payment_method: str = Field(min_length=1, max_length=40)
    note: str | None = Field(default="", max_length=1000)
    save_address: bool = False
    accept_terms: bool = False


@router.post("/store/checkout/quote")
def checkout_quote(body: QuoteIn, request: Request, response: Response, db: Session = Depends(get_db),
                   p: Principal = Depends(principal)):
    cart = resolve_cart(db, request, response, p, create=False)
    q = build_quote(db, p.store, cart, body.address.model_dump(), body.payment_method, body.shipping_option_id)
    q.pop("_internal")
    db.commit()
    return q


@router.post("/store/checkout")
def checkout(body: CheckoutIn, request: Request, response: Response, db: Session = Depends(get_db),
             p: Principal = Depends(principal)):
    limiter.hit(f"checkout:{p.ip}", 20, 600)
    if body.email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", body.email.strip()):
        raise AppError("invalid_email", "Enter a valid email address", details={"field": "email"})
    return place_order(db, request, response, p, body)


# ================================================================ storefront: orders
def _public_order(db: Session, o: Order) -> dict:
    from ..payments.models import PaymentIntent, PaymentMethodConfig
    from ..payments.service import catalog_entry, latest_intent
    from ..shipping.models import Shipment, ShipmentEvent

    lines = lines_of(db, o.id)
    events = db.execute(select(OrderEvent).where(OrderEvent.order_id == o.id, OrderEvent.public.is_(True))
                        .order_by(OrderEvent.created_at)).scalars()
    it = latest_intent(db, o.id)
    cfg = db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.store_id == o.store_id,
                                                       PaymentMethodConfig.key == o.payment_method)).scalar_one_or_none()
    entry = catalog_entry(o.payment_method) or {"label": o.payment_method, "kind": "manual"}
    ships = []
    for s in db.execute(select(Shipment).where(Shipment.order_id == o.id, Shipment.status != "cancelled")
                        .order_by(Shipment.created_at)).scalars():
        evs = db.execute(select(ShipmentEvent).where(ShipmentEvent.shipment_id == s.id).order_by(ShipmentEvent.at)
                         ).scalars()
        ships.append({"courier": s.courier_key, "tracking_code": s.tracking_code, "tracking_url": s.tracking_url,
                      "status": s.status, "is_simulation": s.is_simulation,
                      "events": [{"status": e.status, "message": e.message, "at": e.at.isoformat()} for e in evs]})
    can_retry = bool(o.status == "awaiting_payment" and entry.get("kind") == "redirect" and it is not None
                     and it.status in ("failed", "cancelled", "requires_action"))
    payment = {"method": o.payment_method, "label": (cfg.label if cfg and cfg.label else entry["label"]),
               "kind": entry.get("kind"), "status": o.payment_status, "can_retry": can_retry,
               "intent_status": it.status if it else None,
               "redirect_url": it.redirect_url if it and it.status == "requires_action" else None}
    if o.payment_method == "bank_transfer" and cfg:
        payment["instructions"] = {k: v for k, v in (cfg.settings or {}).items() if v}
        payment["reference_submitted"] = bool(it and it.customer_reference)
    _ = PaymentIntent
    return {
        "number": o.number, "status": o.status, "status_label": STATUS_LABELS.get(o.status, o.status),
        "payment_status": o.payment_status, "fulfillment_status": o.fulfillment_status,
        "placed_at": o.placed_at.isoformat(), "currency": o.currency, "is_test": o.is_test,
        "lines": [{"id": str(ln.id), "title": ln.title, "variant_title": ln.variant_title, "sku": ln.sku,
                   "image": {"url": ln.image_url, "alt": ln.title} if ln.image_url else None,
                   "quantity": ln.quantity, "unit_price_minor": ln.unit_price_minor, "total_minor": ln.total_minor}
                  for ln in lines],
        "totals": {"subtotal_minor": o.subtotal_minor, "discount_minor": o.discount_minor,
                   "shipping_minor": o.shipping_minor, "payment_fee_minor": o.payment_fee_minor,
                   "tax_minor": o.tax_minor, "tax_inclusive": o.tax_inclusive, "total_minor": o.total_minor,
                   "paid_minor": o.paid_minor, "refunded_minor": o.refunded_minor},
        "discount_code": o.discount_code, "shipping_label": o.shipping_label,
        "shipping_address": o.shipping_address, "payment": payment,
        "timeline": [{"kind": e.kind, "message": e.message, "at": e.created_at.isoformat()} for e in events],
        "shipments": ships,
        "can_cancel": o.status in ("pending", "awaiting_payment") and o.paid_minor == 0,
        "expires_at": o.expires_at.isoformat() if o.expires_at else None,
    }


def _order_for_viewer(db: Session, p: Principal, number: str, token: str | None, lock: bool = False) -> Order:
    limiter.hit(f"order-view:{p.ip}", 60, 60)
    try:
        o = get_order(db, p.store.id, number, lock=lock)
    except NotFound:
        raise NotFound("Order") from None
    if p.user is not None and o.user_id == p.user.id:
        return o
    if check_access_token(o, token):
        return o
    raise NotFound("Order")  # same response as a missing order: no enumeration


@router.get("/store/orders/{number}")
def store_order(number: str, token: str | None = None, db: Session = Depends(get_db),
                p: Principal = Depends(principal)):
    return _public_order(db, _order_for_viewer(db, p, number, token))


@router.post("/store/orders/{number}/pay")
def retry_payment(number: str, token: str | None = None, db: Session = Depends(get_db),
                  p: Principal = Depends(principal)):
    from ..payments.service import checkout_methods, start_payment

    limiter.hit(f"retry-pay:{p.ip}", 10, 600)
    o = _order_for_viewer(db, p, number, token, lock=True)
    if o.status != "awaiting_payment":
        raise AppError("payment_not_retryable", "This order is not waiting for payment", status=409)
    method = next((m for m in checkout_methods(db, p.store, o.total_minor) if m["key"] == o.payment_method), None)
    if method is None or not method["available"] or method["kind"] != "redirect":
        raise AppError("payment_method_unavailable", (method or {}).get("unavailable_reason")
                       or "This payment method is not available right now", status=409)
    nxt = start_payment(db, p.store.id, o, o.payment_method)
    timeline(db, o, "payment", "Customer started a new payment attempt", public=True)
    db.commit()
    return {"next": nxt}


class BankRefIn(BaseModel):
    reference: str = Field(min_length=3, max_length=120)


@router.post("/store/orders/{number}/bank-reference")
def bank_reference(number: str, body: BankRefIn, token: str | None = None, db: Session = Depends(get_db),
                   p: Principal = Depends(principal)):
    from ..payments.service import submit_bank_reference

    o = _order_for_viewer(db, p, number, token, lock=True)
    submit_bank_reference(db, o, body.reference)
    db.commit()
    return _public_order(db, o)


@router.post("/store/orders/{number}/cancel")
def customer_cancel(number: str, token: str | None = None, db: Session = Depends(get_db),
                    p: Principal = Depends(principal)):
    from ...core.services import Actor

    o = _order_for_viewer(db, p, number, token, lock=True)
    if o.status not in ("pending", "awaiting_payment") or o.paid_minor > 0:
        raise AppError("not_cancellable", "This order can no longer be cancelled online. Contact the store.",
                       status=409)
    cancel_order(db, o, Actor("customer", p.user.id if p.user else None, o.email or o.phone, p.ip),
                 "Cancelled by customer", by_customer=True)
    db.commit()
    return _public_order(db, o)


@router.get("/store/track")
def track(number: str = Query(min_length=3, max_length=24), phone: str = Query(min_length=6, max_length=32),
          db: Session = Depends(get_db), p: Principal = Depends(principal)):
    from ..shipping.models import Shipment, ShipmentEvent

    limiter.hit(f"track:{p.ip}", 10, 60)
    try:
        ph = normalize_bd_mobile(phone)
    except InvalidPhone:
        raise NotFound("Order") from None
    o = db.execute(select(Order).where(Order.store_id == p.store.id, Order.number == number.strip().upper(),
                                       Order.phone == ph)).scalar_one_or_none()
    if o is None:
        raise NotFound("Order")
    ships = []
    for s in db.execute(select(Shipment).where(Shipment.order_id == o.id, Shipment.status != "cancelled")).scalars():
        evs = db.execute(select(ShipmentEvent).where(ShipmentEvent.shipment_id == s.id).order_by(ShipmentEvent.at)
                         ).scalars()
        ships.append({"courier": s.courier_key, "tracking_code": s.tracking_code, "status": s.status,
                      "events": [{"status": e.status, "message": e.message, "at": e.at.isoformat()} for e in evs]})
    return {"number": o.number, "status": o.status, "status_label": STATUS_LABELS.get(o.status, o.status),
            "fulfillment_status": o.fulfillment_status, "placed_at": o.placed_at.isoformat(), "shipments": ships}


# ================================================================ customer account
def _account_customer(db: Session, p: Principal, create: bool = True):
    from ..customers.models import Customer

    c = db.execute(select(Customer).where(Customer.store_id == p.store.id, Customer.user_id == p.user.id)
                   ).scalar_one_or_none()
    if c is None and create:
        c = Customer(store_id=p.store.id, user_id=p.user.id, email=p.user.email, name=p.user.name or "",
                     phone=p.user.phone)
        db.add(c)
        db.flush()
    return c


@router.get("/account/orders")
def account_orders(db: Session = Depends(get_db), p: Principal = Depends(customer)):
    rows = db.execute(select(Order).where(Order.store_id == p.store.id, Order.user_id == p.user.id)
                      .order_by(Order.placed_at.desc()).limit(200)).scalars().all()
    counts = dict(db.execute(select(OrderLine.order_id, func.sum(OrderLine.quantity))
                             .where(OrderLine.order_id.in_([o.id for o in rows])).group_by(OrderLine.order_id)).all()
                  ) if rows else {}
    return {"items": [{"number": o.number, "placed_at": o.placed_at.isoformat(), "status": o.status,
                       "status_label": STATUS_LABELS.get(o.status, o.status), "payment_status": o.payment_status,
                       "total_minor": o.total_minor, "currency": o.currency, "item_count": int(counts.get(o.id, 0))}
                      for o in rows]}


@router.get("/account/orders/{number}")
def account_order(number: str, db: Session = Depends(get_db), p: Principal = Depends(customer)):
    o = get_order(db, p.store.id, number)
    if o.user_id != p.user.id:
        raise NotFound("Order")
    return _public_order(db, o)


class AccountAddressIn(BaseModel):
    label: str = Field(default="Home", max_length=40)
    name: str = Field(min_length=1, max_length=160)
    phone: str = Field(min_length=6, max_length=32)
    line1: str = Field(min_length=1, max_length=300)
    line2: str = Field(default="", max_length=300)
    area: str = Field(default="", max_length=120)
    upazila: str = Field(default="", max_length=120)
    district: str = Field(min_length=1, max_length=120)
    division: str = Field(default="", max_length=120)
    postal_code: str = Field(default="", max_length=12)
    is_default: bool = False


class AccountAddressPatch(BaseModel):
    label: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    line1: str | None = Field(default=None, max_length=300)
    line2: str | None = Field(default=None, max_length=300)
    area: str | None = Field(default=None, max_length=120)
    upazila: str | None = Field(default=None, max_length=120)
    district: str | None = Field(default=None, max_length=120)
    division: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=12)
    is_default: bool | None = None


def _addr_out(a) -> dict:
    return {"id": str(a.id), "label": a.label, "name": a.name, "phone": a.phone, "line1": a.line1, "line2": a.line2,
            "area": a.area, "upazila": a.upazila, "district": a.district, "division": a.division,
            "postal_code": a.postal_code, "is_default": a.is_default}


def _clean_address(data: dict) -> dict:
    from ..shipping.locations import resolve

    if "phone" in data and data["phone"] is not None:
        try:
            data["phone"] = normalize_bd_mobile(data["phone"])
        except InvalidPhone as exc:
            raise AppError("invalid_phone", str(exc), details={"field": "phone"}) from exc
    if data.get("district") is not None:
        loc = resolve(data["district"], data.get("upazila"))
        if loc is None:
            raise AppError("invalid_address", "Choose a valid district", details={"field": "district"})
        if data.get("upazila") and loc["upazila"] is None:
            raise AppError("invalid_address", "That upazila/thana is not in the selected district",
                           details={"field": "upazila"})
        data["district"], data["division"] = loc["district"], loc["division"]
        if loc["upazila"]:
            data["upazila"] = loc["upazila"]
    return data


@router.get("/account/addresses")
def list_addresses(db: Session = Depends(get_db), p: Principal = Depends(customer)):
    from ..customers.models import Address

    c = _account_customer(db, p, create=False)
    if c is None:
        return {"items": []}
    rows = db.execute(select(Address).where(Address.customer_id == c.id)
                      .order_by(Address.is_default.desc(), Address.created_at)).scalars()
    return {"items": [_addr_out(a) for a in rows]}


def _set_default(db: Session, customer_id, keep_id) -> None:
    from ..customers.models import Address

    for a in db.execute(select(Address).where(Address.customer_id == customer_id, Address.id != keep_id,
                                              Address.is_default.is_(True))).scalars():
        a.is_default = False


@router.post("/account/addresses")
def create_address(body: AccountAddressIn, db: Session = Depends(get_db), p: Principal = Depends(customer)):
    from ..customers.models import Address

    c = _account_customer(db, p)
    data = _clean_address(body.model_dump())
    count = db.execute(select(func.count()).select_from(Address).where(Address.customer_id == c.id)).scalar_one()
    if count >= 50:
        raise AppError("too_many_addresses", "You can save up to 50 addresses")
    a = Address(customer_id=c.id, **data)
    a.is_default = data["is_default"] or count == 0
    db.add(a)
    db.flush()
    if a.is_default:
        _set_default(db, c.id, a.id)
    db.commit()
    return _addr_out(a)


def _own_address(db: Session, p: Principal, address_id: str):
    from ..customers.models import Address

    c = _account_customer(db, p, create=False)
    a = db.get(Address, parse_uuid(address_id, "Address"))
    if c is None or a is None or a.customer_id != c.id:
        raise NotFound("Address")
    return c, a


@router.patch("/account/addresses/{address_id}")
def update_address(address_id: str, body: AccountAddressPatch, db: Session = Depends(get_db),
                   p: Principal = Depends(customer)):
    c, a = _own_address(db, p, address_id)
    data = _clean_address({k: v for k, v in body.model_dump().items() if v is not None})
    for k, v in data.items():
        setattr(a, k, v)
    if data.get("is_default"):
        _set_default(db, c.id, a.id)
    db.commit()
    return _addr_out(a)


@router.delete("/account/addresses/{address_id}")
def delete_address(address_id: str, db: Session = Depends(get_db), p: Principal = Depends(customer)):
    _, a = _own_address(db, p, address_id)
    db.delete(a)
    db.commit()
    return {"ok": True}


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    marketing_consent: bool | None = None


def _profile(p: Principal, c) -> dict:
    return {"name": p.user.name, "email": p.user.email, "phone": p.user.phone,
            "marketing_consent": bool(c.marketing_consent if c else p.user.marketing_consent)}


@router.get("/account/profile")
def get_profile(db: Session = Depends(get_db), p: Principal = Depends(customer)):
    return _profile(p, _account_customer(db, p, create=False))


@router.patch("/account/profile")
def patch_profile(body: ProfilePatch, db: Session = Depends(get_db), p: Principal = Depends(customer)):
    c = _account_customer(db, p)
    if body.name is not None:
        p.user.name = body.name.strip()
        c.name = p.user.name
    if body.phone is not None:
        if body.phone.strip() == "":
            p.user.phone = None
        else:
            try:
                p.user.phone = normalize_bd_mobile(body.phone)
            except InvalidPhone as exc:
                raise AppError("invalid_phone", str(exc), details={"field": "phone"}) from exc
        c.phone = p.user.phone
    if body.marketing_consent is not None and body.marketing_consent != c.marketing_consent:
        c.marketing_consent = p.user.marketing_consent = body.marketing_consent
        c.consent_updated_at = utcnow()
    audit(db, p.store.id, p.actor, "customer.profile_updated", "customer", c.id,
          {"fields": sorted(k for k, v in body.model_dump().items() if v is not None)})
    db.commit()
    return _profile(p, c)


# ================================================================ admin: orders
def _can_pii(p: Principal) -> bool:
    return p.can("customers.pii") or p.can("orders.write") or p.can("shipping.manage")


def _mask_email(e: str | None) -> str | None:
    if not e or "@" not in e:
        return e
    user, dom = e.split("@", 1)
    return user[:1] + "•••@" + dom


def _row(o: Order, pii: bool, item_count: int | None = None) -> dict:
    return {"id": str(o.id), "number": o.number, "placed_at": o.placed_at.isoformat(), "status": o.status,
            "status_label": STATUS_LABELS.get(o.status, o.status), "payment_status": o.payment_status,
            "fulfillment_status": o.fulfillment_status, "payment_method": o.payment_method,
            "total_minor": o.total_minor, "paid_minor": o.paid_minor, "refunded_minor": o.refunded_minor,
            "currency": o.currency, "name": o.name, "phone": o.phone if pii else mask_phone(o.phone),
            "email": o.email if pii else _mask_email(o.email), "district": (o.shipping_address or {}).get("district"),
            "tags": o.tags or [], "is_test": o.is_test, "item_count": item_count}


def _needs_action():
    return or_(Order.status.in_(["pending", "on_hold"]), Order.payment_status == "awaiting_verification",
               and_(Order.status == "confirmed", Order.fulfillment_status == "unfulfilled"))


@router.get("/admin/orders")
def admin_orders(status: str | None = None, payment_status: str | None = None, fulfillment_status: str | None = None,
                 payment_method: str | None = None, date_from: dt.date | None = Query(None, alias="from"),
                 date_to: dt.date | None = Query(None, alias="to"), q: str | None = Query(None, max_length=100),
                 queue: str | None = None, include_test: bool = True, page: int = Query(1, ge=1),
                 page_size: int = Query(50, ge=1, le=200), sort: str = "-placed_at", db: Session = Depends(get_db),
                 p: Principal = Depends(require("orders.read"))):
    tz = ZoneInfo(p.store.timezone or "Asia/Dhaka")
    conds = [Order.store_id == p.store.id]
    if status:
        conds.append(Order.status.in_(status.split(",")))
    if payment_status:
        conds.append(Order.payment_status.in_(payment_status.split(",")))
    if fulfillment_status:
        conds.append(Order.fulfillment_status.in_(fulfillment_status.split(",")))
    if payment_method:
        conds.append(Order.payment_method == payment_method)
    if date_from:
        conds.append(Order.placed_at >= dt.datetime.combine(date_from, dt.time(), tz))
    if date_to:
        conds.append(Order.placed_at < dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time(), tz))
    if queue == "needs_action":
        conds.append(_needs_action())
    if not include_test:
        conds.append(Order.is_test.is_(False))
    if q and q.strip():
        s = q.strip()
        ors = [Order.number.ilike(f"%{s}%"), Order.name.ilike(f"%{s}%"), Order.email.ilike(f"%{s}%")]
        try:
            ors.append(Order.phone == normalize_bd_mobile(s))
        except InvalidPhone:
            digits = re.sub(r"\D", "", s)
            if len(digits) >= 4:
                ors.append(Order.phone.like(f"%{digits[-8:]}%"))
        conds.append(or_(*ors))
    sort_col = {"placed_at": Order.placed_at, "total": Order.total_minor, "number": Order.seq}.get(
        sort.lstrip("-"), Order.placed_at)
    total = db.execute(select(func.count()).select_from(Order).where(*conds)).scalar_one()
    rows = db.execute(select(Order).where(*conds).order_by(sort_col.desc() if sort.startswith("-") else sort_col)
                      .offset((page - 1) * page_size).limit(page_size)).scalars().all()
    counts = dict(db.execute(select(OrderLine.order_id, func.sum(OrderLine.quantity))
                             .where(OrderLine.order_id.in_([o.id for o in rows])).group_by(OrderLine.order_id)).all()
                  ) if rows else {}
    pii = _can_pii(p)
    status_counts = dict(db.execute(select(Order.status, func.count()).where(Order.store_id == p.store.id)
                                    .group_by(Order.status)).all())
    return {"items": [_row(o, pii, int(counts.get(o.id, 0))) for o in rows], "total": total, "page": page,
            "page_size": page_size, "status_counts": status_counts,
            "needs_action": db.execute(select(func.count()).select_from(Order).where(
                Order.store_id == p.store.id, _needs_action())).scalar_one()}


def admin_order_detail(db: Session, p: Principal, o: Order) -> dict:
    from ..customers.models import Customer
    from ..payments.models import PaymentEvent, PaymentIntent, Refund
    from ..payments.service import REFUND_APPROVAL_THRESHOLD, _refundable, refund_dict
    from ..shipping.models import Shipment
    from ..shipping.service import shipment_dict, shipment_events

    pii = _can_pii(p)
    lines = lines_of(db, o.id)
    events = db.execute(select(OrderEvent).where(OrderEvent.order_id == o.id).order_by(OrderEvent.created_at)
                        ).scalars()
    intents = list(db.execute(select(PaymentIntent).where(PaymentIntent.order_id == o.id)
                              .order_by(PaymentIntent.created_at)).scalars())
    pevents = list(db.execute(select(PaymentEvent).where(PaymentEvent.intent_id.in_([i.id for i in intents]))
                              .order_by(PaymentEvent.received_at)).scalars()) if intents else []
    refunds = list(db.execute(select(Refund).where(Refund.order_id == o.id).order_by(Refund.created_at)).scalars())
    ships = list(db.execute(select(Shipment).where(Shipment.order_id == o.id).order_by(Shipment.created_at)).scalars())
    cust = db.get(Customer, o.customer_id) if o.customer_id else None
    allowed = sorted(ORDER_TRANSITIONS.get(o.status, set()) & {"confirmed", "on_hold", "cancelled", "pending",
                                                                "awaiting_payment"})
    if o.status == "on_hold":
        allowed = sorted({o.held_from or "pending", "confirmed", "cancelled"} & ORDER_TRANSITIONS["on_hold"])
    if o.status == "awaiting_payment":
        allowed = [a for a in allowed if a != "confirmed"]  # only a verified payment confirms
    refundable = sum(max(0, _refundable(db, i)) for i in intents if i.captured_minor > 0)
    out = _row(o, pii)
    out.update({
        "fulfillment_status": o.fulfillment_status, "note": o.note, "hold_reason": o.hold_reason,
        "cancel_reason": o.cancel_reason, "held_from": o.held_from,
        "shipping_address": o.shipping_address if pii else {k: v for k, v in (o.shipping_address or {}).items()
                                                            if k in ("district", "division", "upazila")},
        "shipping_label": o.shipping_label, "discount_code": o.discount_code,
        "totals": {"subtotal_minor": o.subtotal_minor, "discount_minor": o.discount_minor,
                   "shipping_minor": o.shipping_minor, "payment_fee_minor": o.payment_fee_minor,
                   "tax_minor": o.tax_minor, "tax_inclusive": o.tax_inclusive, "total_minor": o.total_minor,
                   "paid_minor": o.paid_minor, "refunded_minor": o.refunded_minor},
        "lines": [{"id": str(ln.id), "title": ln.title, "variant_title": ln.variant_title, "sku": ln.sku,
                   "image_url": ln.image_url, "quantity": ln.quantity, "unit_price_minor": ln.unit_price_minor,
                   "total_minor": ln.total_minor, "discount_minor": ln.discount_minor,
                   "reserved_qty": ln.reserved_qty, "fulfilled_qty": ln.fulfilled_qty,
                   "refunded_qty": ln.refunded_qty, "returned_qty": ln.returned_qty,
                   "warehouse_id": str(ln.warehouse_id) if ln.warehouse_id else None,
                   "variant_id": str(ln.variant_id) if ln.variant_id else None} for ln in lines],
        "timeline": [{"id": str(e.id), "kind": e.kind, "message": e.message, "public": e.public,
                      "actor": e.actor_label or e.actor_kind, "at": e.created_at.isoformat()} for e in events],
        "payment": {
            "intents": [{"id": str(i.id), "provider": i.provider, "kind": i.kind, "mode": i.mode,
                         "amount_minor": i.amount_minor, "captured_minor": i.captured_minor,
                         "refunded_minor": i.refunded_minor, "currency": i.currency, "status": i.status,
                         "provider_ref": i.provider_ref, "provider_txn_id": i.provider_txn_id,
                         "customer_reference": i.customer_reference, "failure_reason": i.failure_reason,
                         "remitted_at": i.remitted_at.isoformat() if i.remitted_at else None,
                         "created_at": i.created_at.isoformat()} for i in intents],
            "events": [{"id": str(e.id), "provider": e.provider, "event_id": e.provider_event_id, "type": e.type,
                        "status": e.status, "signature_valid": e.signature_valid, "reason": e.reason,
                        "amount_minor": e.amount_minor, "attempts": e.attempts,
                        "received_at": e.received_at.isoformat()} for e in pevents],
            "refunds": [refund_dict(r) for r in refunds],
            "refundable_minor": refundable,
            "approval_threshold_minor": get_setting(db, p.store.id, REFUND_APPROVAL_THRESHOLD.key),
        },
        "shipments": [shipment_dict(s, shipment_events(db, s.id)) for s in ships],
        "customer": {"id": str(cust.id), "name": cust.name, "orders_count": cust.orders_count,
                     "total_spent_minor": cust.total_spent_minor,
                     "email": cust.email if pii else _mask_email(cust.email),
                     "phone": cust.phone if pii else mask_phone(cust.phone), "registered": bool(cust.user_id)}
        if cust else None,
        "allowed_transitions": allowed,
        "placed_at": o.placed_at.isoformat(), "expires_at": o.expires_at.isoformat() if o.expires_at else None,
        "can": {"write": p.can("orders.write"), "refund": p.can("orders.refund"), "ship": p.can("shipping.manage")},
    })
    return out


@router.get("/admin/orders/{number}")
def admin_order(number: str, db: Session = Depends(get_db), p: Principal = Depends(require("orders.read"))):
    return admin_order_detail(db, p, get_order(db, p.store.id, number))


class TransitionIn(BaseModel):
    to: str
    note: str | None = Field(default=None, max_length=300)


def _do_transition(db: Session, p: Principal, o: Order, to: str, note: str | None) -> None:
    if to not in ORDER_STATUSES:
        raise AppError("invalid_status", "Unknown status")
    if to in ("partially_shipped", "shipped", "delivered", "returned"):
        raise AppError("use_shipments", "Shipping statuses follow shipments; book or update a shipment instead",
                       status=409)
    if to == "confirmed" and o.status == "awaiting_payment":
        raise AppError("payment_required", "Only a verified payment can confirm this order", status=409)
    if to == "cancelled":
        cancel_order(db, o, p.actor, note or "Cancelled by staff")
        return
    if to == "on_hold" and not (note or "").strip():
        raise AppError("reason_required", "Give a reason for the hold", details={"field": "note"})
    transition(db, o, to, p.actor, note)
    audit(db, p.store.id, p.actor, f"orders.{to}", "order", o.id, {"number": o.number, "note": note})


@router.post("/admin/orders/{number}/transition")
def admin_transition(number: str, body: TransitionIn, db: Session = Depends(get_db),
                     p: Principal = Depends(require("orders.write"))):
    o = get_order(db, p.store.id, number, lock=True)
    _do_transition(db, p, o, body.to, body.note)
    db.commit()
    return admin_order_detail(db, p, o)


class BulkIn(BaseModel):
    action: str = Field(pattern="^(confirm|cancel)$")
    numbers: list[str] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=300)


@router.post("/admin/orders/bulk")
def admin_bulk(body: BulkIn, db: Session = Depends(get_db), p: Principal = Depends(require("orders.write"))):
    results = []
    for number in dict.fromkeys(body.numbers):
        try:
            with db.begin_nested():
                o = get_order(db, p.store.id, number, lock=True)
                _do_transition(db, p, o, "confirmed" if body.action == "confirm" else "cancelled", body.note)
            results.append({"number": number, "ok": True})
        except AppError as exc:
            results.append({"number": number, "ok": False, "error": exc.message})
    db.commit()
    return {"results": results, "succeeded": sum(r["ok"] for r in results)}


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


@router.post("/admin/orders/{number}/notes")
def admin_note(number: str, body: NoteIn, db: Session = Depends(get_db),
               p: Principal = Depends(require("orders.write"))):
    o = get_order(db, p.store.id, number)
    timeline(db, o, "note", body.body.strip(), actor=p.actor)
    audit(db, p.store.id, p.actor, "orders.note_added", "order", o.id, {"number": o.number})
    db.commit()
    return admin_order_detail(db, p, o)


class OrderPatch(BaseModel):
    tags: list[str] | None = Field(default=None, max_length=30)


@router.patch("/admin/orders/{number}")
def admin_patch(number: str, body: OrderPatch, db: Session = Depends(get_db),
                p: Principal = Depends(require("orders.write"))):
    o = get_order(db, p.store.id, number, lock=True)
    if body.tags is not None:
        tags = sorted({t.strip()[:40] for t in body.tags if t.strip()})
        if tags != (o.tags or []):
            timeline(db, o, "tag", f"Tags: {', '.join(tags) or '—'}", actor=p.actor)
            o.tags = tags
            audit(db, p.store.id, p.actor, "orders.tagged", "order", o.id, {"tags": tags})
    db.commit()
    return admin_order_detail(db, p, o)


# ================================================================ admin: home
def _revenue_cond():
    return and_(Order.status.notin_(["cancelled", "awaiting_payment"]), Order.is_test.is_(False))


@router.get("/admin/home")
def admin_home(db: Session = Depends(get_db), p: Principal = Depends(staff)):
    from ..catalog.models import Product, Variant
    from ..identity.models import Membership, Role, User
    from ..inventory.models import StockLevel, Warehouse
    from ..payments.models import PaymentMethodConfig, Refund
    from ..shipping.models import Shipment, ShippingRate, ShippingZone

    sid = p.store.id
    tz = ZoneInfo(p.store.timezone or "Asia/Dhaka")
    today = dt.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    out: dict = {"store": {"name": p.store.name, "currency": p.store.currency, "timezone": str(tz)},
                 "generated_at": utcnow().isoformat()}
    if p.can("analytics.read"):
        def rev(since):
            r = db.execute(select(func.coalesce(func.sum(Order.total_minor - Order.refunded_minor), 0), func.count())
                           .where(Order.store_id == sid, _revenue_cond(), Order.placed_at >= since)).one()
            return {"revenue_minor": int(r[0]), "orders": int(r[1])}
        d30 = rev(today - dt.timedelta(days=29))
        spark = dict(db.execute(select(func.date_trunc("day", func.timezone(str(tz), Order.placed_at)),
                                       func.sum(Order.total_minor - Order.refunded_minor))
                                .where(Order.store_id == sid, _revenue_cond(),
                                       Order.placed_at >= today - dt.timedelta(days=13))
                                .group_by(1)).all())
        days = [(today - dt.timedelta(days=13 - i)).date() for i in range(14)]
        by_day = {k.date() if hasattr(k, "date") else k: int(v) for k, v in spark.items()}
        out["kpis"] = {"today": rev(today), "d7": rev(today - dt.timedelta(days=6)), "d30": d30,
                       "aov_30d_minor": d30["revenue_minor"] // d30["orders"] if d30["orders"] else None,
                       "test_orders_30d": db.execute(select(func.count()).select_from(Order).where(
                           Order.store_id == sid, Order.is_test.is_(True),
                           Order.placed_at >= today - dt.timedelta(days=29))).scalar_one(),
                       "basis": "Orders placed (excluding cancelled, unpaid online and test orders), net of refunds"}
        out["sparkline"] = [{"date": d.isoformat(), "revenue_minor": by_day.get(d, 0)} for d in days]
    if p.can("orders.read"):
        def cnt(*conds):
            return db.execute(select(func.count()).select_from(Order).where(Order.store_id == sid, *conds)
                              ).scalar_one()
        out["queues"] = {
            "pending_confirmation": cnt(Order.status == "pending"),
            "awaiting_payment": cnt(Order.status == "awaiting_payment"),
            "transfers_to_verify": cnt(Order.payment_status == "awaiting_verification"),
            "ready_to_ship": cnt(Order.status == "confirmed", Order.fulfillment_status == "unfulfilled"),
            "on_hold": cnt(Order.status == "on_hold"),
            "failed_deliveries": db.execute(select(func.count()).select_from(Shipment).where(
                Shipment.store_id == sid, Shipment.status == "failed_attempt")).scalar_one(),
            "refunds_awaiting_approval": db.execute(select(func.count()).select_from(Refund).where(
                Refund.store_id == sid, Refund.status == "awaiting_approval")).scalar_one(),
        }
        pii = _can_pii(p)
        out["recent_orders"] = [_row(o, pii) for o in db.execute(
            select(Order).where(Order.store_id == sid).order_by(Order.placed_at.desc()).limit(8)).scalars()]
    if p.can("inventory.read"):
        avail = (select(StockLevel.variant_id, func.sum(StockLevel.on_hand - StockLevel.reserved).label("a"),
                        func.sum(StockLevel.reorder_point).label("r"))
                 .join(Warehouse, Warehouse.id == StockLevel.warehouse_id).where(Warehouse.store_id == sid,
                                                                                  Warehouse.is_active.is_(True))
                 .group_by(StockLevel.variant_id).subquery())
        out["low_stock"] = db.execute(
            select(func.count()).select_from(Variant).join(Product, Product.id == Variant.product_id)
            .outerjoin(avail, avail.c.variant_id == Variant.id)
            .where(Variant.store_id == sid, Variant.track_inventory.is_(True), Variant.is_active.is_(True),
                   Product.status == "active", func.coalesce(avail.c.a, 0) <= func.coalesce(avail.c.r, 0))
        ).scalar_one()
    if p.can("settings.read") or p.can("orders.read"):
        from ..payments.providers.catalog import enableable
        from ..payments.service import catalog_entry, payments_mode

        methods = [m for m in db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.store_id == sid,
                                                                           PaymentMethodConfig.enabled.is_(True))
                                         ).scalars()
                   if catalog_entry(m.key) and enableable(catalog_entry(m.key), m, payments_mode())[0]]
        zones = db.execute(select(func.count(func.distinct(ShippingZone.id))).select_from(ShippingZone)
                           .join(ShippingRate, ShippingRate.zone_id == ShippingZone.id)
                           .where(ShippingZone.store_id == sid, ShippingZone.active.is_(True),
                                  ShippingRate.active.is_(True))).scalar_one()
        owners = db.execute(select(User.mfa_enabled_at).join(Membership, Membership.user_id == User.id)
                            .join(Role, Role.id == Membership.role_id)
                            .where(Membership.store_id == sid, Role.key == "owner")).scalars().all()
        products = db.execute(select(func.count()).select_from(Product).where(Product.store_id == sid,
                                                                              Product.status == "active",
                                                                              Product.is_demo.is_(False))
                              ).scalar_one()
        out["setup"] = [
            {"key": "products", "label": "Publish your first product", "done": products > 0,
             "path": "catalog", "detail": f"{products} active product(s)"},
            {"key": "payments", "label": "Enable a payment method", "done": bool(methods), "path": "payments",
             "detail": ", ".join(m.label or m.key for m in methods) or "None enabled"},
            {"key": "shipping", "label": "Set delivery zones and rates", "done": zones > 0, "path": "shipping",
             "detail": f"{zones} zone(s) with active rates"},
            {"key": "email", "label": "Connect outgoing email", "path": "settings",
             "done": bool(get_setting(db, sid, "email.smtp_host")),
             "detail": "SMTP host configured" if get_setting(db, sid, "email.smtp_host")
             else "Order emails are held until SMTP is configured"},
            {"key": "mfa", "label": "Turn on two-step sign-in for owners", "path": "account",
             "done": bool(owners) and all(owners), "detail": f"{sum(1 for x in owners if x)}/{len(owners)} owners"},
        ]
    return out


# ================================================================ admin shell hooks
def _orders_counter(db: Session, p: Principal) -> dict:
    if not p.can("orders.read"):
        return {}
    return {"orders": db.execute(select(func.count()).select_from(Order).where(Order.store_id == p.store.id,
                                                                               _needs_action())).scalar_one()}


def _orders_search(db: Session, p: Principal, q: str) -> list[dict]:
    if not p.can("orders.read"):
        return []
    s = q.strip()
    ors = [Order.number.ilike(f"%{s}%")]
    try:
        ors.append(Order.phone == normalize_bd_mobile(s))
    except InvalidPhone:
        pass
    if "@" in s and (p.can("customers.pii") or p.can("orders.write")):
        ors.append(func.lower(Order.email) == s.lower())
    rows = db.execute(select(Order).where(Order.store_id == p.store.id, or_(*ors))
                      .order_by(Order.placed_at.desc()).limit(8)).scalars()
    return [{"label": o.number, "sublabel": f"{o.name} · ৳{o.total_minor / 100:,.0f} · "
                                            f"{STATUS_LABELS.get(o.status, o.status)}",
             "path": f"/admin/orders/{o.number}", "kind": "Orders"} for o in rows]


NAV_COUNTERS.append(_orders_counter)
SEARCHERS.append(_orders_search)
_ = Forbidden
