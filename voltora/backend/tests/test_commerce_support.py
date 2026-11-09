"""Shared helpers for the commerce/payments/shipping/discount suites (no tests here)."""
from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import select

from tests.conftest import PASSWORD

DHAKA = {"line1": "House 12, Road 5", "area": "Dhanmondi", "upazila": "Dhanmondi", "district": "Dhaka",
         "division": "Dhaka", "postal_code": "1205"}
OUTSIDE = {"line1": "College Road", "upazila": "Debidwar", "district": "Cumilla", "division": "Chattogram"}


def warehouse(db, store, code="MAIN"):
    from app.modules.inventory.models import Warehouse

    w = db.execute(select(Warehouse).where(Warehouse.store_id == store.id, Warehouse.code == code)).scalar_one_or_none()
    if w is None:
        w = Warehouse(store_id=store.id, name=f"Warehouse {code}", code=code, is_default=code == "MAIN")
        db.add(w)
        db.flush()
    return w


def product(db, store, *, price=120000, stock=5, sku=None, title="Test Kettle", track=True, weight=800,
            backorder=False, status="active", wh=None):
    from app.modules.catalog.models import Product, Variant
    from app.modules.inventory.ledger import apply_movement

    sku = sku or f"SKU-{uuid.uuid4().hex[:6]}"
    p = Product(store_id=store.id, title=title, slug=f"{sku.lower()}", status=status)
    db.add(p)
    db.flush()
    v = Variant(store_id=store.id, product_id=p.id, sku=sku, price_minor=price, weight_grams=weight,
                track_inventory=track, allow_backorder=backorder)
    db.add(v)
    db.flush()
    if track and stock:
        w = wh or warehouse(db, store)
        apply_movement(db, store_id=store.id, variant_id=v.id, warehouse_id=w.id, reason="receive",
                       delta_on_hand=stock)
    db.commit()
    return v


def level(db, variant_id):
    from app.modules.inventory.models import StockLevel

    db.expire_all()
    rows = db.execute(select(StockLevel).where(StockLevel.variant_id == variant_id)).scalars().all()
    return sum(r.on_hand for r in rows), sum(r.reserved for r in rows)


def add(api, variant_id, qty=1):
    r = api.post("/api/store/cart/lines", json={"variant_id": str(variant_id), "quantity": qty})
    assert r.status_code == 200, r.text
    return r.json()


def quote(api, address=None, method=None):
    r = api.post("/api/store/checkout/quote", json={"address": {"district": (address or DHAKA)["district"],
                                                                "upazila": (address or DHAKA).get("upazila")},
                                                    "payment_method": method})
    assert r.status_code == 200, r.text
    return r.json()


def checkout(api, method="cod", address=None, key=None, phone="01711-234567", email="buyer@example.com",
             option=None, expect=200, **extra):
    q = quote(api, address, method)
    body = {"idempotency_key": key or f"ck-{uuid.uuid4().hex}", "email": email, "phone": phone,
            "name": "Rahim Uddin", "address": address or DHAKA,
            "shipping_option_id": option or q["shipping_options"][0]["id"], "payment_method": method,
            "note": "", "accept_terms": True, **extra}
    r = api.post("/api/store/checkout", json=body)
    assert r.status_code == expect, r.text
    return r.json(), body


def buy(api, db, store, variant=None, qty=1, method="cod", **kw):
    variant = variant or product(db, store)
    add(api, variant.id, qty)
    out, _ = checkout(api, method=method, **kw)
    return out, variant


def sandbox_signed(db, store, body: dict | str, ts=None):
    from app.modules.payments.service import sandbox_signed as _signed

    raw = body if isinstance(body, str) else json.dumps(body)
    h = _signed(db, store.id, raw, ts)
    db.commit()
    return raw, h


def sandbox_event(intent, type_="payment.succeeded", amount=None, currency=None, event_id=None):
    return {"id": event_id or f"evt_{uuid.uuid4().hex}", "type": type_, "intent_id": str(intent["id"]),
            "amount_minor": intent["amount_minor"] if amount is None else amount,
            "currency": currency or intent["currency"], "livemode": False, "created": int(time.time()),
            "provider_txn_id": "SBXTEST123"}


def intents(owner_api, number):
    return owner_api.get(f"/api/admin/orders/{number}").json()["payment"]["intents"]


def register_customer(api, email="shopper@example.com"):
    r = api.post("/api/auth/register", json={"email": email, "password": PASSWORD, "name": "Shopper"})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def sandbox_account_id(owner_api):
    items = owner_api.get("/api/admin/shipping/couriers").json()["items"]
    return next(c for c in items if c["key"] == "sandbox")["accounts"][0]["id"]
