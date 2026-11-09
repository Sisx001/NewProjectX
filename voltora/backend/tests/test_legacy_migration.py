"""Legacy MongoDB export -> PostgreSQL. Fixtures follow the real legacy shapes (seed.py / schemas.py / commerce.py)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.security import hash_password
from app.modules.catalog.models import Brand, Category, Product, Variant
from app.modules.commerce.models import Order, OrderLine
from app.modules.customers.models import Address, Customer
from app.modules.discounts.models import Discount, DiscountRedemption
from app.modules.identity.models import Membership, User
from app.modules.inventory.models import StockLevel, Warehouse
from app.modules.migration import importer
from app.modules.migration.__main__ import main as cli_main
from app.modules.migration.models import LegacyIdMap
from app.modules.platform.models import SettingValue
from tests.conftest import login

PW = "Legacy-Pass-12345!"


def build_export(d: Path, prefix: str = "") -> Path:
    def w(name, rows, ndjson=False):
        text = "\n".join(json.dumps(r) for r in rows) if ndjson else json.dumps(rows)
        (d / f"{prefix}{name}.{'ndjson' if ndjson else 'json'}").write_text(text)

    w("categories", [{"id": "audio", "name": "Audio", "slug": "audio", "enabled": True, "parent_id": "", "position": 0},
                     {"id": "earbuds", "name": "Earbuds", "slug": "earbuds", "enabled": True, "parent_id": "audio", "position": 1}])
    w("brands", [{"id": "auralis", "name": "Auralis", "slug": "auralis"}])
    w("products", [
        {"id": "p1", "name": "Studio H1", "slug": "studio-h1", "sku": "H1", "brand": "Auralis", "category": "audio", "price": 1890000,
         "compare_price": 2290000, "images": ["/a.webp"], "published": True, "demo": True, "description": "x", "specs": {"Driver": "40mm"},
         "variants": [{"id": "p1-v1", "sku": "H1-1", "options": {"Finish": "Graphite"}, "price": 1890000},
                      {"id": "p1-v2", "sku": "H1-2", "options": {"Finish": "Silver"}, "price": 1990000}], "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": "p2", "name": "Bud 2", "slug": "bud-2", "sku": "B2", "brand": "Unlisted Co", "category": "earbuds", "price": 590000, "published": False,
         "variants": [], "created_at": "2026-01-02T00:00:00+00:00"},
        {"id": "p3", "name": "", "slug": "nameless", "sku": "N1", "price": 1},
        {"id": "p4", "name": "Dup sku", "slug": "dup-sku", "sku": "H1-1", "price": 5, "variants": [{"id": "p4-v1", "sku": "H1-1", "options": {}, "price": 5}]},
    ])
    w("inventory", [{"id": "main", "quantities": {"p1-v1": 6, "p1-v2": 9, "p2:default": 4, "ghost-v": 3},
                     "reservations": {"o-open": "2026-01-05T00:00:00+00:00"}}], ndjson=True)
    w("coupons", [{"id": "c1", "code": "hello10", "type": "percentage", "value": 10, "min_spend": 100000, "max_uses": 100, "max_per_customer": 1,
                   "enabled": True, "starts_at": "", "ends_at": "", "product_ids": ["p1", "gone"], "category_ids": [], "used": 1},
                  {"id": "c2", "code": "BAD", "type": "percentage", "value": 400}])
    w("users", [
        {"id": "initial-owner", "email": "Owner@Legacy.Example", "name": "Legacy Owner", "role": "owner", "password_hash": hash_password(PW),
         "mfa_enabled": False, "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": "u-ord", "email": "orders@legacy.example", "name": "Order Mgr", "role": "order_manager", "password_hash": hash_password(PW)},
        {"id": "u-cust", "email": "sadia@example.com", "name": "Sadia", "role": "customer", "phone": "+8801711000000", "password_hash": hash_password(PW),
         "addresses": [{"label": "Home", "division_id": "div:dhaka", "district_id": "dis:dhaka", "address_line": "Road 1", "phone": "01711000000", "default": True},
                       {"label": "Bad", "address_line": ""}]},
        {"id": "u-old", "email": "old@example.com", "name": "Old", "role": "customer", "password_hash": "pbkdf2:legacy-not-argon"},
        {"id": "u-custom", "email": "custom@legacy.example", "name": "Custom", "role": "my-custom-role", "password_hash": hash_password(PW)},
    ])
    base = {"currency": "BDT", "payment_method": "cod", "payment_status": "unpaid", "history": [{"status": "pending", "at": "2026-01-05T00:00:00+00:00", "note": "placed"}],
            "internal_notes": [{"text": "call first", "at": "2026-01-05T01:00:00+00:00", "actor": "staff"}], "refunds": [], "refunded_amount": 0,
            "tax_inclusive": True, "shipping_method": "Inside Dhaka", "created_at": "2026-01-05T00:00:00+00:00", "updated_at": "2026-01-05T00:00:00+00:00"}
    cust = lambda n, p, e: {"name": n, "phone": p, "email": e, "address": "House 1", "city": "Dhaka", "postal_code": "1205",  # noqa: E731
                            "location": {"district_id": {"id": "dis:dhaka", "name": "Dhaka"}, "division_id": {"id": "div:dhaka", "name": "Dhaka"}}}
    item = lambda pid, vid, q, price: {"product_id": pid, "variant_id": vid, "name": "N", "sku": "S", "options": {"Finish": "Graphite"}, "quantity": q,  # noqa: E731
                                       "price": price, "total": price * q, "tier_saving": 0}
    w("orders", [
        {**base, "id": "o-open", "number": "VT-OPEN0001", "idempotency_key": "k-open-000000000001", "status": "pending", "user_id": None,
         "customer": cust("Rahim", "01711223344", "rahim@example.com"), "items": [item("p1", "p1-v1", 2, 1890000)], "subtotal": 3780000, "shipping": 8000,
         "total": 3788000, "marketing": True},
        {**base, "id": "o-done", "number": "VT-DONE0002", "idempotency_key": "k-done-000000000002", "status": "delivered", "payment_status": "paid",
         "user_id": "u-cust", "customer": cust("Sadia", "+8801711000000", "sadia@example.com"), "items": [item("p1", "p1-v2", 1, 1990000)], "subtotal": 1990000,
         "discount": 199000, "coupon": "HELLO10", "shipping": 8000, "tax": 0, "total": 1799000,
         "history": [{"status": "delivered", "at": "2026-01-09T00:00:00+00:00", "note": "ok"}], "refunded_amount": 100000,
         "refunds": [{"id": "r1", "at": "2026-01-10T00:00:00+00:00", "amount": 100000, "method": "manual", "lines": []}]},
        {**base, "id": "o-mismatch", "number": "VT-MISM0003", "idempotency_key": "k-mism-000000000003", "status": "processing", "user_id": None,
         "customer": cust("Karim", "01811223355", None), "items": [item("p1", "ghost", 1, 100)], "subtotal": 100, "shipping": 0, "total": 150},
        {**base, "id": "o-init", "number": "VT-INIT0004", "idempotency_key": "k-init-000000000004", "status": "initiating", "customer": cust("X", "01711", None), "items": [], "total": 1},
        {**base, "id": "o-nophone", "number": "VT-NOPH0005", "idempotency_key": "k-noph-000000000005", "status": "pending", "customer": {"name": "No Phone"}, "items": [], "total": 1},
    ])
    w("settings", [{"id": "store", "value": {"tagline": "Hello world", "announcement": "Sale", "email": "shop@legacy.example", "phone": "01700000000",
                                              "mode": "maintenance", "brand": "VOLTORA", "shipping": [{"id": "dhaka"}], "features": {"a": True},
                                              "payments": [], "tax_rate": 5}}], ndjson=True)
    w("locations", [{"id": "dis:dhaka", "kind": "district", "name": "Dhaka"}, {"id": "div:dhaka", "kind": "division", "name": "Dhaka"}])
    w("sessions", [{"id": "s1"}])
    return d


def counts(db):
    return {n: db.execute(select(func.count()).select_from(m)).scalar() for n, m in (
        ("products", Product), ("variants", Variant), ("categories", Category), ("brands", Brand), ("orders", Order), ("lines", OrderLine),
        ("users", User), ("customers", Customer), ("discounts", Discount), ("addresses", Address), ("levels", StockLevel), ("map", LegacyIdMap))}


@pytest.fixture
def export(tmp_path):
    return build_export(tmp_path)


def test_dry_run_writes_nothing_and_reports(db, store, export):
    before = counts(db)
    rep = importer.run_import(db, store, str(export), apply=False)
    assert counts(db) == before
    c = rep["collections"]
    assert c["products"]["created"] == 2 and c["products"]["invalid"] == 2  # nameless + duplicate sku
    reasons = {(i["collection"], i["legacy_id"]): i["reason"] for i in rep["issues"] if i["severity"] == "invalid"}
    assert "no name" in reasons[("products", "p3")] and "already exists" in reasons[("products", "p4")]
    assert "initiating" in reasons[("orders", "o-init")] or "not importable" in reasons[("orders", "o-init")]
    assert "phone" in reasons[("orders", "o-nophone")]
    assert reasons[("coupons", "c2")].startswith("percentage out of range")
    assert rep["not_migrated"]["sessions"].startswith("transient")
    assert {"shipping", "features", "payments", "brand"} <= set(rep["unmapped_settings"])
    assert "store.tagline" not in rep["unmapped_settings"] and "tagline" not in rep["unmapped_settings"]
    assert "role 'my-custom-role'" in " ".join(i["reason"] for i in rep["issues"])


def test_apply_is_idempotent_and_reconciles(db, store, export):
    rep = importer.run_import(db, store, str(export), apply=True)
    first = counts(db)
    assert first["products"] == 2 and first["variants"] == 3 and first["orders"] == 3 and first["lines"] == 3
    assert first["discounts"] == 1 and first["categories"] == 2 and first["brands"] == 2  # auralis + auto-created "Unlisted Co"
    rec = rep["reconciliation"]
    assert rec["orders"]["ok"], rec["orders"]
    assert rec["orders"]["target_total_minor"] == 3788000 + 1799000 + 150
    assert rec["orders"]["target_refunded_minor"] == 100000
    assert rec["stock"]["ok"] and rec["stock"]["target_available_units"] == 6 + 9 + 4
    second = importer.run_import(db, store, str(export), apply=True)
    assert counts(db) == first
    assert second["totals"]["created"] == 0
    assert second["collections"]["orders"]["skipped_existing"] == 3

    # hierarchy, brand fallback, default variant, product category
    earbuds = db.execute(select(Category).where(Category.slug == "earbuds")).scalar_one()
    assert earbuds.parent_id == db.execute(select(Category.id).where(Category.slug == "audio")).scalar_one()
    p2 = db.execute(select(Product).where(Product.legacy_id == "p2")).scalar_one()
    assert p2.status == "draft" and db.get(Brand, p2.brand_id).name == "Unlisted Co"
    assert db.execute(select(Variant).where(Variant.legacy_id == "p2:default")).scalar_one().price_minor == 590000


def test_stock_reservations_and_orders(db, store, export):
    importer.run_import(db, store, str(export), apply=True)
    wh = db.execute(select(Warehouse).where(Warehouse.code == "MAIN")).scalar_one()
    v1 = db.execute(select(Variant).where(Variant.legacy_id == "p1-v1")).scalar_one()
    lvl = db.get(StockLevel, (v1.id, wh.id))
    assert (lvl.on_hand, lvl.reserved) == (6 + 2, 2)  # legacy quantity is net of the open order's reservation
    assert db.execute(select(func.count()).select_from(StockLevel)).scalar() == 3  # ghost variant not created
    o = db.execute(select(Order).where(Order.number == "VT-DONE0002")).scalar_one()
    assert (o.status, o.payment_status, o.fulfillment_status, o.paid_minor, o.refunded_minor) == ("delivered", "paid", "fulfilled", 1799000, 100000)
    assert o.discount_minor == 199000 and o.delivered_at is not None and o.customer_id and o.user_id
    assert db.execute(select(func.count()).select_from(DiscountRedemption)).scalar() == 1
    d = db.execute(select(Discount)).scalar_one()
    assert d.code == "HELLO10" and d.percent == 10 and d.used_count == 1 and len(d.product_ids) == 1
    mism = db.execute(select(Order).where(Order.number == "VT-MISM0003")).scalar_one()
    assert mism.total_minor == 150  # legacy total is authoritative; difference is a warning
    assert mism.phone == "01811223355"
    cust = db.get(Customer, o.customer_id)
    assert cust.orders_count == 1 and cust.total_spent_minor == 1799000 and cust.marketing_consent is False


def test_users_roles_and_login(db, store, export, api):
    from app.modules.identity.api import ensure_builtin_roles

    ensure_builtin_roles(db, store.id)
    rep = importer.run_import(db, store, str(export), apply=True)
    owner = db.execute(select(User).where(User.email == "owner@legacy.example")).scalar_one()
    assert owner.kind == "staff" and owner.password_hash.startswith("$argon2")
    roles = {m.user_id: m for m in db.execute(select(Membership)).scalars()}
    assert owner.id in roles and db.execute(select(User).where(User.email == "custom@legacy.example")).scalar_one().id not in roles
    assert db.execute(select(User).where(User.email == "old@example.com")).scalar_one().password_hash is None
    assert any("password" in i["reason"] for i in rep["issues"] if i["legacy_id"] == "u-old")
    login(api, "owner@legacy.example", PW)  # legacy argon2 hash works unchanged
    assert api.get("/api/auth/me").status_code == 200
    addr = db.execute(select(Address)).scalars().all()
    assert len(addr) == 1 and addr[0].district == "Dhaka" and addr[0].line1 == "Road 1"


def test_settings_mapping_does_not_overwrite_customised(db, store, export):
    from app.core.services import SYSTEM, get_setting, set_settings

    set_settings(db, store.id, {"store.announcement": "Mine"}, SYSTEM)
    db.commit()
    importer.run_import(db, store, str(export), apply=True)
    assert get_setting(db, store.id, "store.tagline") == "Hello world"
    assert get_setting(db, store.id, "store.announcement") == "Mine"
    assert get_setting(db, store.id, "store.maintenance_mode") is True
    assert get_setting(db, store.id, "store.support_email") == "shop@legacy.example"
    assert db.get(SettingValue, (store.id, "store.tagline")) is not None


def test_rerun_never_resets_live_stock(db, store, export):
    from app.modules.inventory.ledger import apply_movement

    importer.run_import(db, store, str(export), apply=True)
    v = db.execute(select(Variant).where(Variant.legacy_id == "p1-v2")).scalar_one()
    wh = db.execute(select(Warehouse).where(Warehouse.code == "MAIN")).scalar_one()
    apply_movement(db, store_id=store.id, variant_id=v.id, warehouse_id=wh.id, reason="sale", delta_on_hand=-5)
    db.commit()
    importer.run_import(db, store, str(export), apply=True)
    assert db.get(StockLevel, (v.id, wh.id)).on_hand == 4


def test_prefixed_ndjson_export_and_cli(db, store, tmp_path, capsys):
    d = tmp_path / "pre"
    d.mkdir()
    build_export(d, prefix="v1_")
    assert cli_main(["import", "--source", str(d), "--prefix", "v1_", "--report", str(tmp_path / "r.json")]) == 1  # invalid rows -> exit 1
    out = capsys.readouterr().out
    assert "mode: dry_run" in out and "INVALID products/p3" in out
    rep = json.loads((tmp_path / "r.json").read_text())
    assert rep["mode"] == "dry_run" and rep["collections"]["orders"]["created"] == 3
    assert db.execute(select(func.count()).select_from(Order)).scalar() == 0


def test_missing_target_modules_are_reported_not_fatal(db, store, export, monkeypatch):
    monkeypatch.setattr(importer, "_model", lambda module, cls: None)
    rep = importer.run_import(db, store, str(export), apply=False)
    assert "orders" in rep["skipped_targets"] and "coupons" in rep["skipped_targets"]
    assert rep["collections"]["products"]["created"] == 2


REAL = Path(os.environ.get("VOLTORA_LEGACY_EXPORT", "/tmp/legacy-export"))


@pytest.mark.skipif(not (REAL / "orders.ndjson").exists(), reason="no real legacy export available (see scripts/export-legacy-mongo.sh)")
def test_real_legacy_export_round_trip(db, store):
    rep = importer.run_import(db, store, str(REAL), apply=True)
    assert rep["totals"]["invalid"] == 0, [i for i in rep["issues"] if i["severity"] == "invalid"][:5]
    assert rep["reconciliation_ok"], rep["reconciliation"]
    assert rep["collections"]["orders"]["created"] >= 1 and rep["collections"]["products"]["created"] >= 50
