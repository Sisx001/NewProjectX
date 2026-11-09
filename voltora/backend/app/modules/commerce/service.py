"""Cart, server-side pricing, checkout placement and unpaid-order expiry."""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from fastapi import Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.errors import AppError, Conflict
from ...core.security import new_token, token_hash
from ...core.services import SYSTEM, Actor, audit, emit, get_setting, periodic, request_fingerprint, setting
from ...db import utcnow
from ..catalog.models import Product, ProductCategory, ProductMedia, Variant
from ..inventory.models import StockLevel, Warehouse
from .models import Cart, CartLine, Order, OrderLine
from .orders import (
    AUTO_CONFIRM_COD,
    BANK_EXPIRY,
    EV_ORDER_PLACED,
    UNPAID_EXPIRY,
    cancel_order,
    order_access_token,
    timeline,
)
from .phone import InvalidPhone, normalize_bd_mobile

CART_COOKIE = "vt_cart"
MAX_LINE_QTY = 100
MAX_CART_LINES = 100

TAX_RATE = setting(key="tax.vat_rate_bps", type="int", default=0, min=0, max=5000, group="Tax",
                   label="VAT rate (basis points)", description="1500 = 15%. 0 disables VAT on orders.")
TAX_INCLUSIVE = setting(key="tax.prices_include_tax", type="bool", default=True, group="Tax",
                        label="Catalog prices include VAT",
                        description="When on, VAT is shown as included; when off it is added at checkout. "
                                    "Shipping and payment fees are not taxed.")


# ---------------------------------------------------------------- cart identity
def _cookie(response: Response, raw: str | None) -> None:
    if raw is None:
        response.delete_cookie(CART_COOKIE, path="/")
    else:
        response.set_cookie(CART_COOKIE, raw, max_age=30 * 86400, httponly=True, samesite="lax", path="/",
                            secure=get_settings().cookie_secure)


def _merge_into(db: Session, src: Cart, dst: Cart) -> None:
    existing = {ln.variant_id: ln for ln in db.execute(select(CartLine).where(CartLine.cart_id == dst.id)).scalars()}
    for ln in db.execute(select(CartLine).where(CartLine.cart_id == src.id)).scalars():
        if ln.variant_id in existing:
            existing[ln.variant_id].quantity = min(MAX_LINE_QTY, existing[ln.variant_id].quantity + ln.quantity)
            db.delete(ln)
        else:
            ln.cart_id = dst.id
    if not dst.discount_code and src.discount_code:
        dst.discount_code = src.discount_code
    src.status = "merged"


def resolve_cart(db: Session, request: Request, response: Response, p, create: bool) -> Cart | None:
    """Signed-in users own one active cart; a guest cookie cart merges into it on first sight after sign-in."""
    store = p.store
    raw = request.cookies.get(CART_COOKIE)
    guest = None
    if raw:
        guest = db.execute(select(Cart).where(Cart.token_hash == token_hash(raw), Cart.store_id == store.id,
                                              Cart.status == "active").with_for_update()).scalar_one_or_none()
    if p.user is not None:
        cart = db.execute(select(Cart).where(Cart.user_id == p.user.id, Cart.store_id == store.id,
                                             Cart.status == "active").with_for_update()).scalar_one_or_none()
        if guest is not None and guest.user_id is None:
            if cart is None:
                guest.user_id, guest.token_hash = p.user.id, None
                cart = guest
            else:
                _merge_into(db, guest, cart)
            _cookie(response, None)
        elif guest is not None and guest.user_id != p.user.id:
            _cookie(response, None)
        if cart is None and create:
            cart = Cart(store_id=store.id, user_id=p.user.id, currency=store.currency)
            db.add(cart)
            db.flush()
        return cart
    if guest is not None and guest.user_id is not None:
        guest = None  # a cart that belongs to an account is never reachable by cookie alone
    if guest is None and create:
        raw = new_token(24)
        guest = Cart(store_id=store.id, token_hash=token_hash(raw), currency=store.currency)
        db.add(guest)
        db.flush()
        _cookie(response, raw)
    return guest


# ---------------------------------------------------------------- pricing
def availability(db: Session, variant_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not variant_ids:
        return {}
    rows = db.execute(select(StockLevel.variant_id, func.sum(StockLevel.on_hand - StockLevel.reserved))
                      .join(Warehouse, Warehouse.id == StockLevel.warehouse_id)
                      .where(StockLevel.variant_id.in_(variant_ids), Warehouse.is_active.is_(True))
                      .group_by(StockLevel.variant_id)).all()
    return {v: max(0, int(n or 0)) for v, n in rows}


@dataclass
class PricedLine:
    line_id: str | None
    variant: Variant | None
    product: Product | None
    quantity: int
    unit_price_minor: int = 0
    line_total_minor: int = 0
    available: int | None = None
    image: dict | None = None
    category_ids: set = field(default_factory=set)
    issues: list[str] = field(default_factory=list)

    @property
    def sellable(self) -> bool:
        return not self.issues


def price_items(db: Session, store_id: uuid.UUID, items: list[tuple[str | None, uuid.UUID, int]]) -> list[PricedLine]:
    vids = [v for _, v, _ in items]
    variants = {v.id: v for v in db.execute(select(Variant).where(Variant.id.in_(vids),
                                                                  Variant.store_id == store_id)).scalars()}
    pids = {v.product_id for v in variants.values()}
    products = {p.id: p for p in db.execute(select(Product).where(Product.id.in_(pids))).scalars()} if pids else {}
    media: dict = {}
    for m in db.execute(select(ProductMedia).where(ProductMedia.product_id.in_(pids))
                        .order_by(ProductMedia.position)).scalars() if pids else []:
        media.setdefault(m.product_id, []).append(m)
    cats: dict = {}
    for pc in db.execute(select(ProductCategory).where(ProductCategory.product_id.in_(pids))).scalars() if pids else []:
        cats.setdefault(pc.product_id, set()).add(str(pc.category_id))
    avail = availability(db, [v.id for v in variants.values() if v.track_inventory])
    out = []
    for line_id, vid, qty in items:
        v = variants.get(vid)
        prod = products.get(v.product_id) if v else None
        pl = PricedLine(line_id, v, prod, qty)
        if v is None or prod is None or prod.status != "active" or not v.is_active:
            pl.issues.append("unavailable")
            out.append(pl)
            continue
        pl.unit_price_minor = v.price_minor
        pl.line_total_minor = v.price_minor * qty
        pl.category_ids = cats.get(prod.id, set())
        imgs = media.get(prod.id, [])
        pick = next((m for m in imgs if str(v.id) in [str(x) for x in (m.variant_ids or [])]), imgs[0] if imgs else None)
        pl.image = {"url": pick.url, "alt": pick.alt or prod.title} if pick else None
        if v.track_inventory:
            pl.available = avail.get(v.id, 0)
            if not v.allow_backorder:
                if pl.available <= 0:
                    pl.issues.append("out_of_stock")
                elif qty > pl.available:
                    pl.issues.append("insufficient_stock")
        out.append(pl)
    return out


def cart_items(db: Session, cart: Cart | None) -> list[tuple[str, uuid.UUID, int]]:
    if cart is None:
        return []
    return [(str(ln.id), ln.variant_id, ln.quantity) for ln in db.execute(
        select(CartLine).where(CartLine.cart_id == cart.id).order_by(CartLine.added_at, CartLine.id)).scalars()]


def apply_discount(db: Session, store_id: uuid.UUID, code: str | None, lines: list[PricedLine], subtotal: int,
                   customer_key: str | None = None):
    from ..discounts.service import DiscountInvalid, evaluate

    if not code:
        return None, None
    try:
        return evaluate(db, store_id, code, [{"product_id": pl.product.id, "category_ids": pl.category_ids,
                                              "total_minor": pl.line_total_minor} for pl in lines if pl.sellable],
                        subtotal, customer_key), None
    except DiscountInvalid as exc:
        return None, exc.message


def compute_tax(db: Session, store_id: uuid.UUID, taxable_minor: int) -> tuple[int, bool]:
    rate = int(get_setting(db, store_id, TAX_RATE.key) or 0)
    inclusive = bool(get_setting(db, store_id, TAX_INCLUSIVE.key))
    if rate <= 0 or taxable_minor <= 0:
        return 0, inclusive
    if inclusive:
        return (taxable_minor * rate + (10000 + rate) // 2) // (10000 + rate), True
    return (taxable_minor * rate + 5000) // 10000, False


def cart_payload(db: Session, store, cart: Cart | None) -> dict:
    priced = price_items(db, store.id, cart_items(db, cart))
    subtotal = sum(pl.line_total_minor for pl in priced if pl.sellable)
    issues: list[str] = []
    result, err = apply_discount(db, store.id, cart.discount_code if cart else None, priced, subtotal)
    if err:
        issues.append(err)
    if any(not pl.sellable for pl in priced):
        issues.append("Some items need your attention before checkout")
    return {
        "id": str(cart.id) if cart else None, "currency": store.currency,
        "item_count": sum(pl.quantity for pl in priced),
        "lines": [{"id": pl.line_id, "variant_id": str(pl.variant.id) if pl.variant else None,
                   "product_slug": pl.product.slug if pl.product else None,
                   "title": pl.product.title if pl.product else "Unavailable item",
                   "variant_title": pl.variant.title if pl.variant and pl.variant.title != "Default" else "",
                   "image": pl.image, "quantity": pl.quantity, "unit_price_minor": pl.unit_price_minor,
                   "compare_at_minor": pl.variant.compare_at_minor if pl.variant else None,
                   "line_total_minor": pl.line_total_minor, "available": pl.available, "issues": pl.issues}
                  for pl in priced],
        "subtotal_minor": subtotal, "discount_minor": result.amount_minor if result else 0,
        "discount": {"code": result.discount.code, "description": result.discount.description,
                     "free_shipping": result.free_shipping} if result else None,
        "issues": issues,
    }


def add_line(db: Session, cart: Cart, variant_id: uuid.UUID, quantity: int, store_id: uuid.UUID) -> None:
    if quantity < 1 or quantity > MAX_LINE_QTY:
        raise AppError("invalid_quantity", f"Choose between 1 and {MAX_LINE_QTY}")
    ln = db.execute(select(CartLine).where(CartLine.cart_id == cart.id, CartLine.variant_id == variant_id)
                    ).scalar_one_or_none()
    new_qty = quantity + (ln.quantity if ln else 0)
    check_stock(db, store_id, variant_id, new_qty)
    if ln:
        ln.quantity = min(new_qty, MAX_LINE_QTY)
    else:
        if db.execute(select(func.count()).select_from(CartLine).where(CartLine.cart_id == cart.id)).scalar_one() \
                >= MAX_CART_LINES:
            raise AppError("cart_full", f"A cart can hold up to {MAX_CART_LINES} different items")
        db.add(CartLine(cart_id=cart.id, variant_id=variant_id, quantity=new_qty))
    cart.updated_at = utcnow()


def check_stock(db: Session, store_id: uuid.UUID, variant_id: uuid.UUID, qty: int) -> None:
    pl = price_items(db, store_id, [(None, variant_id, qty)])[0]
    if "unavailable" in pl.issues:
        raise Conflict("unavailable", "This item is no longer available")
    if "out_of_stock" in pl.issues or "insufficient_stock" in pl.issues:
        raise Conflict("insufficient_stock", f"Only {pl.available or 0} left in stock",
                       details={"variant_id": str(variant_id), "available": pl.available or 0})


# ---------------------------------------------------------------- quote
def build_quote(db: Session, store, cart: Cart | None, address: dict, payment_method: str | None,
                shipping_option_id: str | None = None, customer_key: str | None = None) -> dict:
    from ..payments.service import checkout_methods
    from ..shipping.service import shipping_options

    priced = price_items(db, store.id, cart_items(db, cart))
    sellable = [pl for pl in priced if pl.sellable]
    subtotal = sum(pl.line_total_minor for pl in sellable)
    result, discount_error = apply_discount(db, store.id, cart.discount_code if cart else None, sellable, subtotal,
                                            customer_key)
    discount = result.amount_minor if result else 0
    merch = subtotal - discount
    weight = sum((pl.variant.weight_grams or 0) * pl.quantity for pl in sellable)
    options = shipping_options(db, store.id, address, merch, weight, bool(result and result.free_shipping))
    chosen = next((o for o in options if o["id"] == shipping_option_id), None) if shipping_option_id else \
        (options[0] if options else None)
    shipping = chosen["amount_minor"] if chosen else 0
    tax, inclusive = compute_tax(db, store.id, merch)
    pre_fee = merch + shipping + (0 if inclusive else tax)
    methods = checkout_methods(db, store, pre_fee, cod_allowed=bool(chosen and chosen["cod_allowed"]),
                               cod_fee_minor=chosen["cod_fee_minor"] if chosen else 0)
    pm = next((m for m in methods if m["key"] == payment_method and m["available"]), None) if payment_method else None
    fee = pm["fee_minor"] if pm else 0
    return {
        "shipping_options": [{k: o[k] for k in ("id", "label", "amount_minor", "eta_label", "cod_allowed")}
                             | {"zone": o["zone"]} for o in options],
        "payment_methods": methods,
        "totals": {"subtotal_minor": subtotal, "discount_minor": discount, "shipping_minor": shipping,
                   "payment_fee_minor": fee, "tax_minor": tax, "tax_inclusive": inclusive,
                   "total_minor": pre_fee + fee},
        "currency": store.currency,
        "issues": ([discount_error] if discount_error else []) +
                  (["No delivery option covers this address"] if not options else []) +
                  (["Some items are unavailable"] if len(sellable) != len(priced) else []),
        "_internal": {"priced": priced, "discount": result, "chosen": chosen, "method": pm, "weight": weight},
    }


# ---------------------------------------------------------------- checkout
def _validate_address(addr: dict) -> dict:
    from ..shipping import locations

    loc = locations.resolve(addr.get("district"), addr.get("upazila"))
    if loc is None:
        raise AppError("invalid_address", "Choose a valid district", details={"field": "address.district"})
    if addr.get("upazila") and loc["upazila"] is None:
        raise AppError("invalid_address", "That upazila/thana is not in the selected district",
                       details={"field": "address.upazila"})
    if addr.get("division") and addr["division"].strip().lower() != loc["division"].lower():
        raise AppError("invalid_address", "District and division do not match", details={"field": "address.division"})
    if not (addr.get("line1") or "").strip():
        raise AppError("invalid_address", "Enter the street address", details={"field": "address.line1"})
    return {"line1": addr["line1"].strip()[:300], "line2": (addr.get("line2") or "").strip()[:300],
            "area": (addr.get("area") or "").strip()[:120], "upazila": loc["upazila"] or "",
            "district": loc["district"], "division": loc["division"],
            "postal_code": (addr.get("postal_code") or "").strip()[:12]}


def _allocate(db: Session, store_id: uuid.UUID, priced: list[PricedLine]) -> dict[uuid.UUID, uuid.UUID | None]:
    """Lock every (variant, active warehouse) row in a stable order, then choose one warehouse per line."""
    from ..inventory.ledger import InsufficientStock, lock_levels

    tracked = [pl for pl in priced if pl.variant.track_inventory]
    if not tracked:
        return {}
    whs = list(db.execute(select(Warehouse).where(Warehouse.store_id == store_id, Warehouse.is_active.is_(True))
                          .order_by(Warehouse.is_default.desc(), Warehouse.created_at)).scalars())
    if not whs:
        raise Conflict("no_warehouse", "The store has no active warehouse to fulfil from")
    levels = lock_levels(db, [(pl.variant.id, w.id) for pl in tracked for w in whs])
    choice: dict[uuid.UUID, uuid.UUID | None] = {}
    for pl in tracked:
        best = None
        for w in whs:
            lv = levels[(pl.variant.id, w.id)]
            if lv.on_hand - lv.reserved >= pl.quantity:
                best = w.id
                break
        if best is None and pl.variant.allow_backorder:
            best = whs[0].id
        if best is None:
            total = sum(max(0, levels[(pl.variant.id, w.id)].on_hand - levels[(pl.variant.id, w.id)].reserved)
                        for w in whs)
            raise InsufficientStock(pl.variant.id, total, pl.quantity)
        choice[pl.variant.id] = best
    return choice


def _upsert_customer(db: Session, store_id: uuid.UUID, p, name: str, phone: str, email: str | None):
    from ..customers.models import Customer

    c = None
    if p.user is not None:
        c = db.execute(select(Customer).where(Customer.store_id == store_id, Customer.user_id == p.user.id)
                       .with_for_update()).scalar_one_or_none()
        if c is None:
            c = Customer(store_id=store_id, user_id=p.user.id, email=p.user.email, phone=phone, name=name)
            db.add(c)
    else:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"customer:{store_id}:{phone}"})
        c = db.execute(select(Customer).where(Customer.store_id == store_id, Customer.user_id.is_(None),
                                              Customer.phone == phone).limit(1).with_for_update()
                       ).scalar_one_or_none()
        if c is None and email:
            c = db.execute(select(Customer).where(Customer.store_id == store_id, Customer.user_id.is_(None),
                                                  func.lower(Customer.email) == email.lower()).limit(1)
                           .with_for_update()).scalar_one_or_none()
        if c is None:
            c = Customer(store_id=store_id, email=email, phone=phone, name=name)
            db.add(c)
    c.name = c.name or name
    c.phone = c.phone or phone
    c.email = c.email or email
    db.flush()
    return c


def order_response(db: Session, order: Order) -> dict:
    from ..payments.service import next_for

    return {"order": {"number": order.number, "access_token": order_access_token(order), "status": order.status,
                      "payment_status": order.payment_status, "total_minor": order.total_minor,
                      "currency": order.currency},
            "next": next_for(db, order)}


def place_order(db: Session, request: Request, response: Response, p, body) -> dict:
    from ..discounts.service import redeem
    from ..inventory.ledger import apply_movement
    from ..payments.service import start_payment
    from ..platform.api import notify

    store = p.store
    if get_setting(db, store.id, "store.maintenance_mode"):
        raise AppError("store_closed", "The store is in maintenance mode. Please try again later.", status=503)
    if not body.accept_terms:
        raise AppError("terms_required", "Please accept the terms to place your order",
                       details={"field": "accept_terms"})
    try:
        phone = normalize_bd_mobile(body.phone)
    except InvalidPhone as exc:
        raise AppError("invalid_phone", str(exc), details={"field": "phone"}) from exc
    email = (body.email or "").strip().lower() or None
    if get_setting(db, store.id, REQUIRE_EMAIL.key) and not email:
        raise AppError("email_required", "Enter your email address", details={"field": "email"})
    fp = request_fingerprint(body.model_dump(mode="json"))
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"checkout:{store.id}:{body.idempotency_key}"})
    existing = db.execute(select(Order).where(Order.store_id == store.id,
                                              Order.idempotency_key == body.idempotency_key)).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fp:
            raise Conflict("idempotency_mismatch", "This checkout key was already used for a different order")
        out = order_response(db, existing)
        db.commit()
        return out
    address = _validate_address(body.address.model_dump())
    cart = resolve_cart(db, request, response, p, create=False)
    if cart is None or not cart_items(db, cart):
        raise AppError("cart_empty", "Your cart is empty")
    q = build_quote(db, store, cart, address, body.payment_method, body.shipping_option_id, f"phone:{phone}")
    internal = q.pop("_internal")
    priced: list[PricedLine] = internal["priced"]
    bad = [pl for pl in priced if not pl.sellable]
    if bad:
        pl = bad[0]
        if "unavailable" in pl.issues:
            raise Conflict("unavailable", f"{pl.product.title if pl.product else 'An item'} is no longer available",
                           details={"variant_id": str(pl.variant.id) if pl.variant else None})
        raise Conflict("insufficient_stock", f"Only {pl.available or 0} of {pl.product.title} left in stock",
                       details={"variant_id": str(pl.variant.id), "available": pl.available or 0})
    if internal["chosen"] is None or (body.shipping_option_id and internal["chosen"]["id"] != body.shipping_option_id):
        raise AppError("shipping_unavailable", "Choose a delivery option available for this address",
                       details={"field": "shipping_option_id"})
    method = internal["method"]
    if method is None:
        reason = next((m["unavailable_reason"] for m in q["payment_methods"] if m["key"] == body.payment_method), None)
        raise AppError("payment_method_unavailable", reason or "Choose an available payment method",
                       details={"field": "payment_method"})
    warehouses = _allocate(db, store.id, priced)
    totals = q["totals"]
    seq = db.execute(select(func.nextval("order_number_seq"))).scalar_one()
    kind = method["kind"]
    order = Order(id=uuid.uuid4(), store_id=store.id, seq=seq, number=f"VT-{seq}",
                  idempotency_key=body.idempotency_key, request_hash=fp, access_token_hash="",
                  user_id=p.user.id if p.user else None, email=email, phone=phone, name=body.name.strip(),
                  status="pending" if kind == "cod" else "awaiting_payment", payment_status="unpaid",
                  payment_method=method["key"], currency=store.currency, subtotal_minor=totals["subtotal_minor"],
                  discount_minor=totals["discount_minor"], shipping_minor=totals["shipping_minor"],
                  payment_fee_minor=totals["payment_fee_minor"], tax_minor=totals["tax_minor"],
                  tax_inclusive=totals["tax_inclusive"], total_minor=totals["total_minor"],
                  discount_code=internal["discount"].discount.code if internal["discount"] else None,
                  shipping_rate_id=uuid.UUID(internal["chosen"]["id"]), shipping_label=internal["chosen"]["label"],
                  shipping_address={**address, "name": body.name.strip(), "phone": phone},
                  note=(body.note or "").strip()[:1000], is_test=bool(method.get("sandbox")))
    order.access_token_hash = token_hash(order_access_token(order))
    if kind == "redirect":
        order.expires_at = utcnow() + dt.timedelta(minutes=int(get_setting(db, store.id, UNPAID_EXPIRY.key)))
    elif kind == "manual":
        order.expires_at = utcnow() + dt.timedelta(hours=int(get_setting(db, store.id, BANK_EXPIRY.key)))
    db.add(order)
    db.flush()
    discount_alloc = _allocate_discount(priced, totals["discount_minor"])
    for pl in priced:
        wh = warehouses.get(pl.variant.id)
        ln = OrderLine(order_id=order.id, variant_id=pl.variant.id, product_id=pl.product.id, sku=pl.variant.sku,
                       title=pl.product.title, variant_title=pl.variant.title if pl.variant.title != "Default" else "",
                       image_url=pl.image["url"] if pl.image else None, quantity=pl.quantity,
                       unit_price_minor=pl.unit_price_minor, discount_minor=discount_alloc.get(pl.line_id, 0),
                       total_minor=pl.line_total_minor, weight_grams=pl.variant.weight_grams or 0, warehouse_id=wh,
                       reserved_qty=pl.quantity if wh else 0)
        db.add(ln)
        if wh:
            apply_movement(db, store_id=store.id, variant_id=pl.variant.id, warehouse_id=wh, reason="reserve",
                           delta_reserved=pl.quantity, ref_type="order", ref_id=order.number,
                           allow_negative_available=pl.variant.allow_backorder)
    if internal["discount"] is not None:
        redeem(db, internal["discount"], order.id, f"phone:{phone}")
    customer = _upsert_customer(db, store.id, p, order.name, phone, email)
    customer.orders_count += 1
    customer.total_spent_minor += order.total_minor
    customer.last_order_at = utcnow()
    order.customer_id = customer.id
    if body.save_address and p.user is not None:
        _save_address(db, customer.id, order.shipping_address)
    start_payment(db, store.id, order, method["key"])
    actor = Actor("customer" if p.user else "anonymous", p.user.id if p.user else None,
                  p.user.email if p.user else email or phone, getattr(p, "ip", None))
    timeline(db, order, "placed", f"Order placed — {method['label']}", public=True, actor=actor,
             data={"total_minor": order.total_minor})
    if kind == "cod" and get_setting(db, store.id, AUTO_CONFIRM_COD.key):
        from .orders import transition

        transition(db, order, "confirmed", SYSTEM, "Auto-confirmed (COD)")
    cart.status, cart.order_id = "converted", order.id
    emit(db, store.id, EV_ORDER_PLACED, {"order_id": str(order.id), "number": order.number,
                                         "total_minor": order.total_minor, "payment_method": method["key"]})
    if email:
        from ..messaging.service import queue_message

        link = f"{get_settings().public_url.rstrip('/')}/order/{order.number}?token={order_access_token(order)}"
        queue_message(db, store.id, channel="email", to=email, template="order.placed", sensitive=True,
                      context={"name": order.name, "order_number": order.number,
                               "total": f"৳{order.total_minor / 100:,.2f}", "payment_method": method["label"],
                               "link": link}, related={"order_id": str(order.id), "order_number": order.number})
    notify(db, store.id, "order.placed", f"New order {order.number}",
           f"৳{order.total_minor / 100:,.2f} · {method['label']}", f"/admin/orders/{order.number}", "orders.read")
    audit(db, store.id, actor, "orders.placed", "order", order.id, {"number": order.number,
                                                                    "total_minor": order.total_minor})
    out = order_response(db, order)
    db.commit()
    return out


def _allocate_discount(priced: list[PricedLine], discount: int) -> dict:
    """Spread an order discount across lines proportionally (largest remainder) for refunds/reporting."""
    total = sum(pl.line_total_minor for pl in priced)
    if discount <= 0 or total <= 0:
        return {}
    shares = {pl.line_id: discount * pl.line_total_minor // total for pl in priced}
    rest = discount - sum(shares.values())
    for pl in sorted(priced, key=lambda x: -x.line_total_minor)[:rest]:
        shares[pl.line_id] += 1
    return shares


def _save_address(db: Session, customer_id: uuid.UUID, addr: dict) -> None:
    from ..customers.models import Address

    dup = db.execute(select(Address.id).where(Address.customer_id == customer_id, Address.line1 == addr["line1"],
                                              Address.district == addr["district"])).first()
    if dup:
        return
    has_default = db.execute(select(Address.id).where(Address.customer_id == customer_id,
                                                      Address.is_default.is_(True))).first()
    db.add(Address(customer_id=customer_id, name=addr.get("name", ""), phone=addr.get("phone", ""),
                   line1=addr["line1"], line2=addr.get("line2", ""), area=addr.get("area", ""),
                   upazila=addr.get("upazila", ""), district=addr["district"], division=addr.get("division", ""),
                   postal_code=addr.get("postal_code", ""), is_default=has_default is None))


REQUIRE_EMAIL = setting(key="checkout.require_email", type="bool", default=False, group="Checkout",
                        label="Require an email address at checkout",
                        description="Phone is always required (couriers need it). Email enables order emails.")


# ---------------------------------------------------------------- expiry
def expire_unpaid_orders(db: Session, now: dt.datetime | None = None, limit: int = 50) -> int:
    from ..payments.models import PaymentIntent

    now = now or utcnow()
    rows = db.execute(select(Order).where(Order.status == "awaiting_payment", Order.expires_at.is_not(None),
                                          Order.expires_at <= now).order_by(Order.expires_at).limit(limit)
                      .with_for_update(skip_locked=True)).scalars().all()
    n = 0
    for o in rows:
        if o.paid_minor > 0:
            continue
        recent = db.execute(select(PaymentIntent.id).where(
            PaymentIntent.order_id == o.id, PaymentIntent.status == "processing",
            PaymentIntent.updated_at > now - dt.timedelta(minutes=15))).first()
        if recent:
            continue  # customer finished the hosted page; give the signed webhook time to arrive
        cancel_order(db, o, SYSTEM, "Payment not received in time")
        for it in db.execute(select(PaymentIntent).where(PaymentIntent.order_id == o.id,
                                                         PaymentIntent.status == "cancelled")).scalars():
            it.status = "expired"
        n += 1
    return n


@periodic("orders.expire_unpaid", every_seconds=60)
def _expire_job(db: Session):
    return {"expired": expire_unpaid_orders(db)}
