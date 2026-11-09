"""Golden journey 5 + 9: cart, server-priced quote, COD checkout with reservation, idempotency, no oversell, IDOR."""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select

from tests.test_commerce_support import (
    DHAKA,
    OUTSIDE,
    add,
    buy,
    checkout,
    level,
    product,
    quote,
    register_customer,
)


def test_bd_phone_normalization():
    from app.modules.commerce.phone import InvalidPhone, normalize_bd_mobile, to_e164

    for raw in ("01711234567", "+8801711234567", "8801711234567", "+880 1711-234567", "০১৭১১২৩৪৫৬৭",
                "008801711234567"):
        assert normalize_bd_mobile(raw) == "01711234567"
    assert to_e164("01711234567") == "+8801711234567"
    for bad in ("", "0171123456", "01211234567", "+14155550100", "abc"):
        with pytest.raises(InvalidPhone):
            normalize_bd_mobile(bad)


def test_locations_tree(api, store):
    r = api.get("/api/store/locations")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    divs = r.json()["divisions"]
    assert len(divs) == 8 and sum(len(d["districts"]) for d in divs) == 64
    dhaka = next(d for v in divs for d in v["districts"] if d["name"] == "Dhaka")
    assert dhaka["bn_name"] == "ঢাকা"
    assert {"Dhanmondi", "Savar"} <= {u["name"] for u in dhaka["upazilas"]}
    assert all(u["bn_name"] for u in dhaka["upazilas"])


def test_cart_lines_stock_and_cookie(api, db, store):
    v = product(db, store, stock=3)
    cart = add(api, v.id, 2)
    assert api.c.cookies.get("vt_cart")
    assert cart["item_count"] == 2 and cart["subtotal_minor"] == 240000 and cart["lines"][0]["available"] == 3
    r = api.post("/api/store/cart/lines", json={"variant_id": str(v.id), "quantity": 2})
    assert r.status_code == 409 and r.json()["error"]["code"] == "insufficient_stock"
    assert r.json()["error"]["details"]["available"] == 3
    line = cart["lines"][0]["id"]
    assert api.patch(f"/api/store/cart/lines/{line}", json={"quantity": 3}).json()["item_count"] == 3
    assert api.patch(f"/api/store/cart/lines/{line}", json={"quantity": 0}).json()["lines"] == []
    add(api, v.id, 1)
    line = api.get("/api/store/cart").json()["lines"][0]["id"]
    assert api.delete(f"/api/store/cart/lines/{line}").json()["item_count"] == 0


def test_cart_isolated_between_guests(new_api, db, store):
    a, b = new_api(), new_api()
    v = product(db, store)
    line = add(a, v.id)["lines"][0]["id"]
    assert b.get("/api/store/cart").json()["lines"] == []
    assert b.patch(f"/api/store/cart/lines/{line}", json={"quantity": 2}).status_code == 404


def test_guest_cart_merges_on_sign_in(api, db, store):
    v1, v2 = product(db, store), product(db, store)
    add(api, v1.id, 1)
    register_customer(api)  # registration signs in; the guest cart is adopted
    cart = api.get("/api/store/cart").json()
    assert cart["item_count"] == 1
    assert api.c.cookies.get("vt_cart") is None
    add(api, v2.id, 2)
    api.post("/api/auth/logout")
    add(api, v1.id, 1)  # new guest cart
    api.post("/api/auth/login", json={"email": "shopper@example.com", "password": "correct-horse-42"})
    cart = api.get("/api/store/cart").json()
    qty = {ln["variant_id"]: ln["quantity"] for ln in cart["lines"]}
    assert qty == {str(v1.id): 2, str(v2.id): 2}


def test_quote_zones_and_server_totals(api, db, store):
    v = product(db, store, price=50000)
    add(api, v.id, 2)
    inside = quote(api, DHAKA, "cod")
    assert inside["shipping_options"][0]["zone"] == "Inside Dhaka"
    assert inside["shipping_options"][0]["amount_minor"] == 8000
    assert quote(api, {"district": "Dhaka", "upazila": "Savar"})["shipping_options"][0]["zone"] == "Dhaka suburbs"
    assert quote(api, {"district": "Gazipur"})["shipping_options"][0]["zone"] == "Dhaka suburbs"
    out = quote(api, OUTSIDE, "cod")
    assert out["shipping_options"][0]["zone"] == "Outside Dhaka"
    t = out["totals"]
    assert t["subtotal_minor"] == 100000 and t["shipping_minor"] == 15000 and t["total_minor"] == 115000
    keys = {m["key"]: m for m in out["payment_methods"]}
    assert keys["cod"]["available"] and keys["sandbox"]["sandbox"] is True
    assert "bank_transfer" not in keys  # disabled by default


def test_cod_checkout_creates_order_with_reservation(api, owner_api, db, store):
    from app.modules.customers.models import Customer
    from app.modules.inventory.models import StockMovement
    from app.modules.messaging.models import OutboundMessage
    from app.modules.platform.models import Notification

    v = product(db, store, stock=5, price=99900)
    add(api, v.id, 2)
    res, _ = checkout(api, "cod", phone="+880 1711 234567", subtotal_minor=1, total_minor=1)  # client totals ignored
    o = res["order"]
    assert o["number"].startswith("VT-") and int(o["number"][3:]) >= 100001
    assert o["status"] == "pending" and o["payment_status"] == "unpaid"
    assert o["total_minor"] == 2 * 99900 + 8000
    assert res["next"] == {"type": "done"}
    assert level(db, v.id) == (5, 2)
    mv = db.execute(select(StockMovement).where(StockMovement.variant_id == v.id, StockMovement.reason == "reserve")
                    ).scalar_one()
    assert mv.ref_id == o["number"] and mv.delta_reserved == 2
    c = db.execute(select(Customer).where(Customer.phone == "01711234567")).scalar_one()
    assert c.orders_count == 1 and c.total_spent_minor == o["total_minor"]
    assert db.execute(select(OutboundMessage).where(OutboundMessage.template == "order.placed")).scalar_one().to \
        == "buyer@example.com"
    assert db.execute(select(Notification).where(Notification.kind == "order.placed")).scalar_one()
    assert api.get("/api/store/cart").json()["item_count"] == 0  # cart converted
    view = api.get(f"/api/store/orders/{o['number']}?token={o['access_token']}").json()
    assert view["status"] == "pending" and view["payment"]["method"] == "cod" and view["timeline"]
    assert view["shipping_address"]["phone"] == "01711234567"
    detail = owner_api.get(f"/api/admin/orders/{o['number']}").json()
    assert detail["lines"][0]["reserved_qty"] == 2 and detail["payment"]["intents"][0]["status"] == "pending"


def test_checkout_validation(api, db, store):
    v = product(db, store)
    r = api.post("/api/store/checkout", json={"idempotency_key": "x" * 20, "phone": "01711234567", "name": "A",
                                              "address": DHAKA, "shipping_option_id": "x", "payment_method": "cod",
                                              "accept_terms": True})
    assert r.status_code == 400 and r.json()["error"]["code"] == "cart_empty"
    add(api, v.id)
    _, body = checkout(api, phone="12345", expect=400)
    assert api.post("/api/store/checkout", json=body).json()["error"]["code"] == "invalid_phone"
    r = api.post("/api/store/checkout", json={**body, "phone": "01711234567", "accept_terms": False,
                                              "idempotency_key": "y" * 20})
    assert r.json()["error"]["code"] == "terms_required"
    r = api.post("/api/store/checkout", json={**body, "phone": "01711234567", "idempotency_key": "z" * 20,
                                              "address": {**DHAKA, "upazila": "Debidwar"}})
    assert r.json()["error"]["code"] == "invalid_address"
    r = api.post("/api/store/checkout", json={**body, "phone": "01711234567", "idempotency_key": "w" * 20,
                                              "payment_method": "bank_transfer"})
    assert r.json()["error"]["code"] == "payment_method_unavailable"


def test_idempotent_checkout_replay(api, db, store):
    v = product(db, store, stock=5)
    add(api, v.id, 1)
    first, body = checkout(api, key="replay-key-0000000001")
    again = api.post("/api/store/checkout", json=body)
    assert again.status_code == 200 and again.json()["order"] == first["order"]
    assert level(db, v.id) == (5, 1)  # reserved once
    r = api.post("/api/store/checkout", json={**body, "note": "different"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_mismatch"


def test_concurrent_buyers_last_unit_exactly_one_order(new_api, db, store):
    from app.modules.commerce.models import Order

    v = product(db, store, stock=1)
    clients = [new_api() for _ in range(6)]
    bodies = []
    for c in clients:
        add(c, v.id, 1)
        q = quote(c, DHAKA, "cod")
        bodies.append({"idempotency_key": f"race-{id(c)}-0000000", "email": None, "phone": "01811234567",
                       "name": "Racer", "address": DHAKA, "shipping_option_id": q["shipping_options"][0]["id"],
                       "payment_method": "cod", "accept_terms": True})
    barrier = threading.Barrier(len(clients))
    results = [None] * len(clients)

    def run(i):
        barrier.wait()
        results[i] = clients[i].post("/api/store/checkout", json=bodies[i])

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(clients))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and codes.count(409) == len(clients) - 1, [r.text for r in results]
    assert all(r.json()["error"]["code"] == "insufficient_stock" for r in results if r.status_code == 409)
    db.expire_all()
    assert db.execute(select(Order)).scalars().all().__len__() == 1
    assert level(db, v.id) == (1, 1)


def test_guest_order_requires_token_idor(api, new_api, db, store):
    res, _ = buy(api, db, store)
    o = res["order"]
    other = new_api()
    assert other.get(f"/api/store/orders/{o['number']}").status_code == 404
    assert other.get(f"/api/store/orders/{o['number']}?token=wrong").status_code == 404
    assert other.get("/api/store/orders/VT-999999?token=x").status_code == 404
    assert other.get(f"/api/store/orders/{o['number']}?token={o['access_token']}").status_code == 200
    assert other.post(f"/api/store/orders/{o['number']}/cancel").status_code == 404
    # public tracking needs the matching phone and exposes no PII
    t = other.get(f"/api/store/track?number={o['number']}&phone=01711234567").json()
    assert t["status"] == "pending" and "shipping_address" not in t and "email" not in str(t)
    assert other.get(f"/api/store/track?number={o['number']}&phone=01911111111").status_code == 404


def test_customer_account_endpoints(api, new_api, db, store):
    register_customer(api)
    res, _ = buy(api, db, store)
    items = api.get("/api/account/orders").json()["items"]
    assert [i["number"] for i in items] == [res["order"]["number"]] and items[0]["item_count"] == 1
    assert api.get(f"/api/account/orders/{res['order']['number']}").status_code == 200
    stranger = new_api()
    register_customer(stranger, "other@example.com")
    assert stranger.get(f"/api/account/orders/{res['order']['number']}").status_code == 404
    assert stranger.get("/api/account/orders").json()["items"] == []
    a = api.post("/api/account/addresses", json={"name": "R", "phone": "+8801711234567", **DHAKA}).json()
    assert a["phone"] == "01711234567" and a["is_default"] is True and a["division"] == "Dhaka"
    assert stranger.patch(f"/api/account/addresses/{a['id']}", json={"label": "x"}).status_code == 404
    assert api.post("/api/account/addresses", json={"name": "R", "phone": "017", **DHAKA}).status_code == 400
    assert api.patch(f"/api/account/addresses/{a['id']}", json={"label": "Office"}).json()["label"] == "Office"
    assert api.delete(f"/api/account/addresses/{a['id']}").json()["ok"]
    prof = api.patch("/api/account/profile", json={"name": "New Name", "phone": "01911111111",
                                                   "marketing_consent": True}).json()
    assert prof == {"name": "New Name", "email": "shopper@example.com", "phone": "01911111111",
                    "marketing_consent": True}
    assert new_api().get("/api/account/orders").status_code == 401


def test_customer_can_cancel_unpaid_order(api, db, store):
    res, v = buy(api, db, store)
    o = res["order"]
    r = api.post(f"/api/store/orders/{o['number']}/cancel?token={o['access_token']}")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert level(db, v.id) == (5, 0)


def test_maintenance_mode_blocks_checkout(api, owner_api, db, store):
    v = product(db, store)
    add(api, v.id)
    owner_api.patch("/api/settings", json={"changes": {"store.maintenance_mode": True}})
    r = checkout(api, expect=503)[0]
    assert r["error"]["code"] == "store_closed"
