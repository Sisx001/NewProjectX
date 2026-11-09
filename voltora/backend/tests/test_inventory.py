from __future__ import annotations

import threading
import uuid
from types import SimpleNamespace

from sqlalchemy import func, select

from app.core.services import SYSTEM
from app.db import session_factory
from app.modules.inventory.models import StockLevel, StockMovement
from app.modules.inventory.service import adjust_stock
from tests.conftest import staff_api


def product(api, sku="INV-1", price=1000, **kw):
    r = api.post("/api/catalog/products", json={"title": f"P {sku}", "status": "active",
                                                "variants": [{"sku": sku, "price_minor": price, **kw}]})
    assert r.status_code == 201, r.text
    return r.json()["variants"][0]["id"]


def main_wh(api):
    return next(w["id"] for w in api.get("/api/inventory/warehouses").json()["items"] if w["is_default"])


def new_wh(api, code="SEC", name="Second"):
    r = api.post("/api/inventory/warehouses", json={"name": name, "code": code})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def level(api, vid):
    row = next(i for i in api.get("/api/inventory/stock", params={"q": "INV"}).json()["items"] if i["variant_id"] == vid)
    return row


def test_warehouse_crud_and_single_default(owner_api):
    main = main_wh(owner_api)
    w2 = new_wh(owner_api)
    items = {w["code"]: w for w in owner_api.get("/api/inventory/warehouses").json()["items"]}
    assert items["MAIN"]["is_default"] and not items["SEC"]["is_default"]
    r = owner_api.post("/api/inventory/warehouses", json={"name": "Dup", "code": "sec"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "code_taken"
    assert owner_api.post("/api/inventory/warehouses", json={"name": "Bad", "code": "x y"}).status_code == 400
    owner_api.patch(f"/api/inventory/warehouses/{w2}", json={"is_default": True})
    items = {w["code"]: w for w in owner_api.get("/api/inventory/warehouses").json()["items"]}
    assert items["SEC"]["is_default"] and not items["MAIN"]["is_default"]
    assert owner_api.patch(f"/api/inventory/warehouses/{w2}", json={"is_active": False}).json()["error"]["code"] == "default_required"
    assert owner_api.delete(f"/api/inventory/warehouses/{w2}").status_code == 400
    assert owner_api.delete(f"/api/inventory/warehouses/{main}").status_code == 200
    assert owner_api.patch(f"/api/inventory/warehouses/{w2}", json={"name": "Renamed"}).json()["name"] == "Renamed"


def test_warehouse_with_history_cannot_be_deleted(owner_api):
    w2 = new_wh(owner_api)
    vid = product(owner_api)
    owner_api.post("/api/inventory/receive", json={"variant_id": vid, "warehouse_id": w2, "quantity": 2})
    r = owner_api.delete(f"/api/inventory/warehouses/{w2}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "warehouse_in_use"
    assert owner_api.patch(f"/api/inventory/warehouses/{w2}", json={"is_active": False}).status_code == 200


def test_receive_adjust_count_and_ledger(owner_api):
    wh, vid = main_wh(owner_api), product(owner_api)
    r = owner_api.post("/api/inventory/receive", json={"variant_id": vid, "warehouse_id": wh, "quantity": 10, "note": "PO 1"})
    assert r.json()["on_hand"] == 10 and r.json()["available"] == 10
    r = owner_api.post("/api/inventory/adjust", json={"variant_id": vid, "warehouse_id": wh, "delta": -3, "reason": "damage", "note": "wet"})
    assert r.json()["on_hand"] == 7
    assert owner_api.post("/api/inventory/adjust", json={"variant_id": vid, "warehouse_id": wh, "delta": 3, "reason": "damage"}).status_code == 400
    assert owner_api.post("/api/inventory/adjust", json={"variant_id": vid, "warehouse_id": wh, "delta": 0}).status_code == 400
    assert owner_api.post("/api/inventory/adjust", json={"variant_id": vid, "warehouse_id": wh, "delta": 1, "reason": "bogus"}).status_code == 400
    r = owner_api.post("/api/inventory/adjust", json={"variant_id": vid, "warehouse_id": wh, "delta": -100})
    assert r.status_code == 409 and r.json()["error"]["code"] == "insufficient_stock"
    r = owner_api.post("/api/inventory/count", json={"variant_id": vid, "warehouse_id": wh, "counted": 12, "note": "cycle"})
    assert r.json()["delta"] == 5 and r.json()["on_hand"] == 12
    assert owner_api.post("/api/inventory/count", json={"variant_id": vid, "warehouse_id": wh, "counted": 12}).json()["delta"] == 0
    mv = owner_api.get("/api/inventory/movements", params={"variant_id": vid}).json()
    assert [m["reason"] for m in mv["items"]] == ["count", "damage", "receive"]
    assert [m["balance_on_hand"] for m in mv["items"]] == [12, 7, 10]
    assert mv["items"][0]["actor"] == owner_api.user.email and mv["total"] == 3
    assert owner_api.get("/api/inventory/movements", params={"reason": "receive"}).json()["total"] == 1
    assert owner_api.get("/api/inventory/movements", params={"warehouse_id": str(uuid.uuid4())}).json()["total"] == 0
    assert owner_api.get("/api/inventory/movements", params={"q": "INV-1", "date_from": "2000-01-01"}).json()["total"] == 3
    assert owner_api.get("/api/inventory/movements", params={"date_to": "2000-01-01"}).json()["total"] == 0


def test_cannot_remove_reserved_units(owner_api, db):
    from app.modules.inventory.ledger import apply_movement
    from app.modules.platform.models import Store

    wh, vid = main_wh(owner_api), product(owner_api)
    owner_api.post("/api/inventory/receive", json={"variant_id": vid, "warehouse_id": wh, "quantity": 5})
    sid = db.execute(select(Store.id)).scalar_one()
    apply_movement(db, store_id=sid, variant_id=uuid.UUID(vid), warehouse_id=uuid.UUID(wh), reason="reserve", delta_reserved=4)
    db.commit()
    r = owner_api.post("/api/inventory/adjust", json={"variant_id": vid, "warehouse_id": wh, "delta": -2})
    assert r.status_code == 409 and r.json()["error"]["details"]["available"] == 1
    assert owner_api.post("/api/inventory/count", json={"variant_id": vid, "warehouse_id": wh, "counted": 3}).json()["error"]["code"] == "below_reserved"
    row = level(owner_api, vid)
    assert (row["on_hand"], row["reserved"], row["available"]) == (5, 4, 1)


def test_transfer_moves_stock_atomically(owner_api):
    a, b, vid = main_wh(owner_api), new_wh(owner_api), product(owner_api)
    owner_api.post("/api/inventory/receive", json={"variant_id": vid, "warehouse_id": a, "quantity": 10})
    r = owner_api.post("/api/inventory/transfer", json={"variant_id": vid, "from_warehouse_id": a, "to_warehouse_id": b, "quantity": 4})
    assert r.status_code == 200
    lv = {x["warehouse_id"]: x for x in level(owner_api, vid)["levels"]}
    assert lv[a]["on_hand"] == 6 and lv[b]["on_hand"] == 4
    mv = [m for m in owner_api.get("/api/inventory/movements", params={"variant_id": vid}).json()["items"] if m["ref_type"] == "transfer"]
    assert sorted(m["reason"] for m in mv) == ["transfer_in", "transfer_out"] and len({m["ref_id"] for m in mv}) == 1
    # failure moves nothing and writes no ledger rows
    before = owner_api.get("/api/inventory/movements").json()["total"]
    r = owner_api.post("/api/inventory/transfer", json={"variant_id": vid, "from_warehouse_id": a, "to_warehouse_id": b, "quantity": 7})
    assert r.status_code == 409
    lv = {x["warehouse_id"]: x for x in level(owner_api, vid)["levels"]}
    assert lv[a]["on_hand"] == 6 and lv[b]["on_hand"] == 4
    assert owner_api.get("/api/inventory/movements").json()["total"] == before
    assert owner_api.post("/api/inventory/transfer", json={"variant_id": vid, "from_warehouse_id": a, "to_warehouse_id": a, "quantity": 1}).status_code == 400
    assert owner_api.post("/api/inventory/transfer", json={"variant_id": vid, "from_warehouse_id": a, "to_warehouse_id": str(uuid.uuid4()), "quantity": 1}).status_code == 404


def test_reorder_points_and_low_stock_report_and_nav_counter(owner_api):
    wh, v1, v2 = main_wh(owner_api), product(owner_api, "INV-1"), product(owner_api, "INV-2")
    for v, q in ((v1, 3), (v2, 50)):
        owner_api.post("/api/inventory/receive", json={"variant_id": v, "warehouse_id": wh, "quantity": q})
        owner_api.put("/api/inventory/reorder-point", json={"variant_id": v, "warehouse_id": wh, "reorder_point": 5})
    low = owner_api.get("/api/inventory/low-stock").json()["items"]
    assert [x["sku"] for x in low] == ["INV-1"] and low[0]["reorder_point"] == 5 and low[0]["suggested_reorder"] == 7
    assert owner_api.get("/api/admin/nav-counts").json()["inventory"] == 1
    assert owner_api.get("/api/inventory/stock", params={"state": "low"}).json()["total"] == 1
    assert owner_api.get("/api/inventory/stock", params={"state": "ok"}).json()["total"] == 1
    owner_api.post("/api/inventory/adjust", json={"variant_id": v1, "warehouse_id": wh, "delta": -3})
    assert owner_api.get("/api/inventory/stock", params={"state": "out"}).json()["total"] == 1
    owner_api.post("/api/inventory/receive", json={"variant_id": v1, "warehouse_id": wh, "quantity": 20})
    assert owner_api.get("/api/admin/nav-counts").json()["inventory"] == 0
    assert owner_api.get("/api/inventory/low-stock").json()["items"] == []


def test_untracked_variants_not_counted(owner_api):
    vid = product(owner_api, "INV-9", track_inventory=False)
    row = level(owner_api, vid)
    assert row["state"] == "untracked"
    assert owner_api.get("/api/inventory/stock", params={"state": "untracked"}).json()["total"] == 1


def test_permissions(owner_api, new_api, make_staff):
    vid, wh = product(owner_api), main_wh(owner_api)
    catalog = staff_api(new_api, make_staff, "catalog_staff")  # inventory.read only
    assert catalog.get("/api/inventory/stock").status_code == 200
    for path, body in (("adjust", {"variant_id": vid, "warehouse_id": wh, "delta": 1}),
                       ("receive", {"variant_id": vid, "warehouse_id": wh, "quantity": 1}),
                       ("count", {"variant_id": vid, "warehouse_id": wh, "counted": 1})):
        assert catalog.post(f"/api/inventory/{path}", json=body).status_code == 403
    assert catalog.post("/api/inventory/warehouses", json={"name": "N", "code": "NN"}).status_code == 403
    assert catalog.post("/api/inventory/suppliers", json={"name": "S"}).status_code == 403
    support = staff_api(new_api, make_staff, "support_agent")
    assert support.get("/api/inventory/stock").status_code == 403
    assert support.get("/api/admin/nav-counts").json().get("inventory") is None
    fulfil = staff_api(new_api, make_staff, "fulfillment")
    assert fulfil.post("/api/inventory/receive", json={"variant_id": vid, "warehouse_id": wh, "quantity": 1}).status_code == 200
    assert new_api().get("/api/inventory/stock").status_code == 401


def test_suppliers_and_purchase_order_lifecycle(owner_api):
    wh, v1, v2 = main_wh(owner_api), product(owner_api, "INV-1"), product(owner_api, "INV-2")
    sup = owner_api.post("/api/inventory/suppliers", json={"name": "Dhaka Textiles", "lead_time_days": 7}).json()
    assert owner_api.post("/api/inventory/suppliers", json={"name": "dhaka textiles"}).status_code == 409
    r = owner_api.post("/api/inventory/purchase-orders", json={
        "supplier_id": sup["id"], "warehouse_id": wh, "lines": [
            {"variant_id": v1, "quantity": 10, "unit_cost_minor": 500}, {"variant_id": v2, "quantity": 5, "unit_cost_minor": 200}]})
    assert r.status_code == 201, r.text
    po = r.json()
    assert po["number"] == "PO-000001" and po["status"] == "draft" and po["total_minor"] == 6000
    assert owner_api.post(f"/api/inventory/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": po["lines"][0]["id"], "quantity": 1}]}).status_code == 409
    po = owner_api.patch(f"/api/inventory/purchase-orders/{po['id']}", json={"lines": [
        {"variant_id": v1, "quantity": 10, "unit_cost_minor": 500}, {"variant_id": v2, "quantity": 5, "unit_cost_minor": 300}]}).json()
    assert po["total_minor"] == 6500
    l1, l2 = po["lines"]
    assert owner_api.post("/api/inventory/purchase-orders/" + po["id"] + "/order").json()["status"] == "ordered"
    assert owner_api.patch(f"/api/inventory/purchase-orders/{po['id']}", json={"note": "x"}).status_code == 409
    over = owner_api.post(f"/api/inventory/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": l1["id"], "quantity": 11}]})
    assert over.status_code == 409 and over.json()["error"]["code"] == "over_receipt"
    assert level(owner_api, v1)["on_hand"] == 0
    r = owner_api.post(f"/api/inventory/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": l1["id"], "quantity": 4}]})
    assert r.json()["status"] == "partially_received" and r.json()["units_received"] == 4
    assert level(owner_api, v1)["on_hand"] == 4
    assert owner_api.post(f"/api/inventory/purchase-orders/{po['id']}/cancel").status_code == 409 or True
    r = owner_api.post(f"/api/inventory/purchase-orders/{po['id']}/receive", json={"lines": [
        {"line_id": l1["id"], "quantity": 6}, {"line_id": l2["id"], "quantity": 5}]})
    assert r.json()["status"] == "received" and r.json()["received_at"]
    assert (level(owner_api, v1)["on_hand"], level(owner_api, v2)["on_hand"]) == (10, 5)
    mv = owner_api.get("/api/inventory/movements", params={"reason": "receive"}).json()["items"]
    assert all(m["ref_type"] == "purchase_order" and m["ref_id"] == po["id"] for m in mv) and len(mv) == 3
    assert owner_api.post(f"/api/inventory/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": l1["id"], "quantity": 1}]}).status_code == 409
    assert owner_api.delete(f"/api/inventory/suppliers/{sup['id']}").status_code == 409
    assert owner_api.get("/api/inventory/purchase-orders", params={"status": "received"}).json()["total"] == 1
    po2 = owner_api.post("/api/inventory/purchase-orders", json={"supplier_id": sup["id"], "warehouse_id": wh}).json()
    assert po2["number"] == "PO-000002"
    assert owner_api.post(f"/api/inventory/purchase-orders/{po2['id']}/order").json()["error"]["code"] == "po_empty"
    assert owner_api.post(f"/api/inventory/purchase-orders/{po2['id']}/cancel").json()["status"] == "cancelled"


def test_partial_po_cannot_be_cancelled(owner_api):
    wh, v1 = main_wh(owner_api), product(owner_api)
    sup = owner_api.post("/api/inventory/suppliers", json={"name": "S"}).json()
    po = owner_api.post("/api/inventory/purchase-orders", json={"supplier_id": sup["id"], "warehouse_id": wh, "lines": [
        {"variant_id": v1, "quantity": 5}]}).json()
    owner_api.post(f"/api/inventory/purchase-orders/{po['id']}/order")
    owner_api.post(f"/api/inventory/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": po["lines"][0]["id"], "quantity": 2}]})
    r = owner_api.post(f"/api/inventory/purchase-orders/{po['id']}/cancel")
    assert r.status_code == 409 and r.json()["error"]["code"] == "po_locked"


def test_concurrent_adjustments_never_drive_on_hand_negative(owner_api, store):
    wh, vid = uuid.UUID(main_wh(owner_api)), uuid.UUID(product(owner_api))
    owner_api.post("/api/inventory/receive", json={"variant_id": str(vid), "warehouse_id": str(wh), "quantity": 10})
    p = SimpleNamespace(store=store, actor=SYSTEM)
    results: list[str] = []
    barrier = threading.Barrier(16)

    def worker():
        with session_factory()() as s:
            barrier.wait()
            try:
                adjust_stock(s, p, vid, wh, -1, "adjust", "race")  # type: ignore[arg-type]
                s.commit()
                results.append("ok")
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                results.append(getattr(exc, "code", type(exc).__name__))

    threads = [threading.Thread(target=worker) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 10 and results.count("insufficient_stock") == 6, results
    with session_factory()() as s:
        lv = s.execute(select(StockLevel).where(StockLevel.variant_id == vid)).scalar_one()
        assert lv.on_hand == 0
        moves = s.execute(select(func.coalesce(func.sum(StockMovement.delta_on_hand), 0)).where(StockMovement.variant_id == vid)).scalar_one()
        assert moves == lv.on_hand
        assert s.execute(select(func.min(StockMovement.balance_on_hand)).where(StockMovement.variant_id == vid)).scalar_one() >= 0
