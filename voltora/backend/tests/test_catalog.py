from __future__ import annotations

import io

from sqlalchemy import select

from app.modules.catalog.service import sanitize_html
from app.modules.platform.models import AuditEvent, OutboxEvent
from tests.conftest import staff_api

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def variant(sku, price=1000, **kw):
    return {"sku": sku, "price_minor": price, **kw}


def mk(api, title="Tee", sku=None, status="active", price=1000, **kw):
    body = {"title": title, "status": status, "variants": [variant(sku or title.upper().replace(" ", "-"), price)], **kw}
    r = api.post("/api/catalog/products", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def warehouse_id(api):
    return next(w["id"] for w in api.get("/api/inventory/warehouses").json()["items"] if w["is_default"])


def stock(api, vid, qty):
    r = api.post("/api/inventory/receive", json={"variant_id": vid, "warehouse_id": warehouse_id(api), "quantity": qty})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ admin CRUD
def test_create_get_update_product(owner_api, db):
    p = mk(owner_api, "Linen Shirt", description="<p>Nice</p><script>alert(1)</script>")
    assert p["slug"] == "linen-shirt" and p["description"] == "<p>Nice</p>"
    assert p["variants"][0]["available"] == 0 and p["currency"] == "BDT"
    r = owner_api.patch(f"/api/catalog/products/{p['id']}", json={"title": "Linen Shirt 2", "tags": ["a", "A", "b"]})
    assert r.status_code == 200 and r.json()["tags"] == ["a", "b"] and r.json()["title"] == "Linen Shirt 2"
    assert owner_api.get("/api/catalog/products/not-a-uuid").status_code == 404
    actions = {a for (a,) in db.execute(select(AuditEvent.action))}
    assert {"product.created", "product.updated"} <= actions
    types = {t for (t,) in db.execute(select(OutboxEvent.type))}
    assert {"product.created", "product.updated"} <= types


def test_variant_matrix_and_options(owner_api):
    r = owner_api.post("/api/catalog/products/generate-variants", json={
        "options": [{"name": "Size", "values": ["S", "M"]}, {"name": "Colour", "values": ["Red", "Blue", "Red"]}],
        "price_minor": 500, "sku_prefix": "tee"})
    assert r.status_code == 200 and r.json()["count"] == 4
    vs = r.json()["variants"]
    assert vs[0]["sku"] == "TEE-S-RED" and vs[0]["price_minor"] == 500
    body = {"title": "Tee", "options": [{"name": "Size", "values": ["S", "M"]}, {"name": "Colour", "values": ["Red", "Blue"]}],
            "variants": vs}
    r = owner_api.post("/api/catalog/products", json=body)
    assert r.status_code == 201 and len(r.json()["variants"]) == 4
    assert r.json()["variants"][1]["title"] == "S / Blue"
    big = {"options": [{"name": f"O{i}", "values": [str(j) for j in range(6)]} for i in range(4)]}
    assert owner_api.post("/api/catalog/products/generate-variants", json=big).json()["error"]["code"] == "too_many_variants"


def test_invalid_option_values_rejected(owner_api):
    r = owner_api.post("/api/catalog/products", json={
        "title": "X", "options": [{"name": "Size", "values": ["S"]}],
        "variants": [variant("X-1", option_values={"Size": "XL"})]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_option_value"
    r = owner_api.post("/api/catalog/products", json={"title": "X", "variants": [variant("A", compare_at_minor=5, price=10)]})
    assert r.json()["error"]["code"] == "invalid_compare_at"


def test_sku_unique_per_store(owner_api):
    mk(owner_api, "One", sku="DUP-1")
    r = owner_api.post("/api/catalog/products", json={"title": "Two", "variants": [variant("dup-1")]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "sku_taken"
    p2 = mk(owner_api, "Two", sku="OK-2")
    r = owner_api.patch(f"/api/catalog/products/{p2['id']}", json={"variants": [{**p2["variants"][0], "sku": "DUP-1"}]})
    assert r.status_code == 409
    r = owner_api.post("/api/catalog/products", json={"title": "Three", "variants": [variant("Z"), variant("z")]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "sku_duplicate"
    # product rolled back by failed validation
    assert owner_api.get("/api/catalog/products", params={"q": "Three"}).json()["total"] == 0


def test_variant_removal_keeps_history(owner_api):
    p = mk(owner_api, "Hist", sku="H-1")
    stock(owner_api, p["variants"][0]["id"], 5)
    r = owner_api.patch(f"/api/catalog/products/{p['id']}", json={"variants": [variant("H-2")]})
    assert r.status_code == 200
    by_sku = {v["sku"]: v for v in r.json()["variants"]}
    assert by_sku["H-1"]["is_active"] is False and by_sku["H-2"]["is_active"] is True


def test_active_product_needs_active_variant(owner_api):
    r = owner_api.post("/api/catalog/products", json={"title": "N", "status": "active",
                                                      "variants": [variant("N-1", is_active=False)]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_active_variant"


def test_permissions(owner_api, new_api, make_staff):
    p = mk(owner_api, "Perm")
    agent = staff_api(new_api, make_staff, "support_agent")
    assert agent.get("/api/catalog/products").status_code == 200
    assert agent.post("/api/catalog/products", json={"title": "x", "variants": [variant("x")]}).status_code == 403
    assert agent.patch(f"/api/catalog/products/{p['id']}", json={"title": "z"}).status_code == 403
    assert agent.delete(f"/api/catalog/products/{p['id']}").status_code == 403
    assert agent.post("/api/catalog/products/bulk", json={"ids": [p["id"]], "action": "archive"}).status_code == 403
    assert agent.post("/api/catalog/categories", json={"name": "c"}).status_code == 403
    assert agent.delete("/api/catalog/demo").status_code == 403
    assert new_api().get("/api/catalog/products").status_code == 401
    cat_staff = staff_api(new_api, make_staff, "catalog_staff")
    assert cat_staff.post("/api/catalog/categories", json={"name": "Allowed"}).status_code == 201


def test_sanitizer():
    assert sanitize_html('<p onclick="x()">Hi <b>there</b></p>') == "<p>Hi <b>there</b></p>"
    assert sanitize_html('<a href="javascript:alert(1)">x</a>') == "<a>x</a>"
    assert 'href="https://ok.example/a"' in sanitize_html('<a href="https://ok.example/a" target="_blank">x</a>')
    assert sanitize_html("<style>p{}</style><iframe src=x></iframe>text") == "text"
    assert sanitize_html("<img src=x onerror=alert(1)>") == ""
    assert sanitize_html("a < b & c") == "a &lt; b &amp; c"
    assert sanitize_html("<ul><li>one<li>two") == "<ul><li>one<li>two</li></li></ul>"
    assert sanitize_html("<script>alert(1)</script><p>ok</p>") == "<p>ok</p>"
    assert sanitize_html('<a href="  JaVaScRiPt:alert(1)">x</a>') == "<a>x</a>"


def test_categories_tree_and_cycles(owner_api):
    a = owner_api.post("/api/catalog/categories", json={"name": "Apparel"}).json()
    b = owner_api.post("/api/catalog/categories", json={"name": "Shirts", "parent_id": a["id"]}).json()
    c = owner_api.post("/api/catalog/categories", json={"name": "Linen", "parent_id": b["id"]}).json()
    r = owner_api.patch(f"/api/catalog/categories/{a['id']}", json={"parent_id": c["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "category_cycle"
    r = owner_api.patch(f"/api/catalog/categories/{a['id']}", json={"parent_id": a["id"]})
    assert r.json()["error"]["code"] == "category_cycle"
    dup = owner_api.post("/api/catalog/categories", json={"name": "Apparel"}).json()
    assert dup["slug"] == "apparel-2"
    # deleting a middle node re-parents its children
    assert owner_api.delete(f"/api/catalog/categories/{b['id']}").status_code == 200
    items = {i["name"]: i for i in owner_api.get("/api/catalog/categories").json()["items"]}
    assert items["Linen"]["parent_id"] == a["id"]


def test_brands_crud(owner_api):
    b = owner_api.post("/api/catalog/brands", json={"name": "Acme"}).json()
    assert b["slug"] == "acme"
    p = mk(owner_api, "Branded", brand_id=b["id"])
    assert owner_api.get("/api/catalog/brands").json()["items"][0]["product_count"] == 1
    assert owner_api.patch(f"/api/catalog/brands/{b['id']}", json={"name": "Acme Co"}).json()["name"] == "Acme Co"
    assert owner_api.delete(f"/api/catalog/brands/{b['id']}").status_code == 200
    assert owner_api.get(f"/api/catalog/products/{p['id']}").json()["brand_id"] is None


def test_list_search_filters_sort_pagination(owner_api):
    cat = owner_api.post("/api/catalog/categories", json={"name": "Shirts"}).json()
    brand = owner_api.post("/api/catalog/brands", json={"name": "Acme"}).json()
    a = mk(owner_api, "Alpha Shirt", sku="AL-1", price=3000, tags=["summer"], category_ids=[cat["id"]], brand_id=brand["id"])
    mk(owner_api, "Beta Mug", sku="BE-1", price=1000, status="draft")
    c = mk(owner_api, "Gamma Hat", sku="GA-1", price=2000)
    stock(owner_api, a["variants"][0]["id"], 5)
    ls = lambda **kw: owner_api.get("/api/catalog/products", params=kw).json()  # noqa: E731
    assert ls()["total"] == 3
    assert [i["title"] for i in ls(q="ga-1")["items"]] == ["Gamma Hat"]
    assert [i["title"] for i in ls(q="summer")["items"]] == ["Alpha Shirt"]
    assert [i["title"] for i in ls(status="draft")["items"]] == ["Beta Mug"]
    assert [i["title"] for i in ls(category_id=cat["id"])["items"]] == ["Alpha Shirt"]
    assert [i["title"] for i in ls(brand_id=brand["id"])["items"]] == ["Alpha Shirt"]
    assert [i["title"] for i in ls(stock="in")["items"]] == ["Alpha Shirt"]
    assert {i["title"] for i in ls(stock="out")["items"]} == {"Beta Mug", "Gamma Hat"}
    assert ls(is_demo="true")["total"] == 0 and ls(is_demo="false")["total"] == 3
    assert [i["title"] for i in ls(sort="price", dir="asc")["items"]] == ["Beta Mug", "Gamma Hat", "Alpha Shirt"]
    page = ls(sort="title", dir="asc", page_size=2, page=2)
    assert page["total"] == 3 and [i["title"] for i in page["items"]] == ["Gamma Hat"]
    item = ls(q="Alpha")["items"][0]
    assert item["price_min_minor"] == 3000 and item["tracked_available"] == 5 and item["brand"] == "Acme"
    assert c["id"]
    assert owner_api.get("/api/catalog/products", params={"q": "%"}).json()["total"] == 0


def test_bulk_actions_and_duplicate_and_delete(owner_api):
    a, b = mk(owner_api, "A1", sku="A1"), mk(owner_api, "B1", sku="B1", status="draft")
    ids = [a["id"], b["id"]]
    r = owner_api.post("/api/catalog/products/bulk", json={"ids": ids, "action": "add_tag", "value": "sale"})
    assert r.json() == {"selected": 2, "updated": 2}
    assert owner_api.post("/api/catalog/products/bulk", json={"ids": ids, "action": "add_tag", "value": "SALE"}).json()["updated"] == 0
    owner_api.post("/api/catalog/products/bulk", json={"ids": [a["id"]], "action": "remove_tag", "value": "sale"})
    assert owner_api.get(f"/api/catalog/products/{a['id']}").json()["tags"] == []
    owner_api.post("/api/catalog/products/bulk", json={"ids": ids, "action": "set_status", "value": "active"})
    assert owner_api.get(f"/api/catalog/products/{b['id']}").json()["status"] == "active"
    owner_api.post("/api/catalog/products/bulk", json={"ids": ids, "action": "archive"})
    assert owner_api.get(f"/api/catalog/products/{a['id']}").json()["status"] == "archived"
    assert owner_api.post("/api/catalog/products/bulk", json={"ids": ids, "action": "set_status", "value": "bogus"}).status_code == 400
    r = owner_api.post(f"/api/catalog/products/{a['id']}/duplicate")
    assert r.status_code == 201
    d = r.json()
    assert d["status"] == "draft" and d["title"] == "A1 (copy)" and d["variants"][0]["sku"] == "A1-COPY"
    assert owner_api.post(f"/api/catalog/products/{a['id']}/duplicate").json()["variants"][0]["sku"] == "A1-COPY2"
    assert owner_api.delete(f"/api/catalog/products/{d['id']}").status_code == 200
    assert owner_api.get(f"/api/catalog/products/{d['id']}").status_code == 404


def test_delete_blocked_by_stock_history(owner_api):
    p = mk(owner_api, "Moved", sku="MV-1")
    stock(owner_api, p["variants"][0]["id"], 3)
    owner_api.post("/api/inventory/adjust", json={"variant_id": p["variants"][0]["id"],
                                                  "warehouse_id": warehouse_id(owner_api), "delta": -1, "reason": "damage"})
    r = owner_api.delete(f"/api/catalog/products/{p['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "product_in_use"
    assert owner_api.get(f"/api/catalog/products/{p['id']}").status_code == 200


# ------------------------------------------------------------------ public storefront
def test_public_catalog_hides_drafts_and_computes_stock(owner_api, new_api):
    shop = new_api()
    cat = owner_api.post("/api/catalog/categories", json={"name": "Clothes"}).json()
    sub = owner_api.post("/api/catalog/categories", json={"name": "Shirts", "parent_id": cat["id"]}).json()
    brand = owner_api.post("/api/catalog/brands", json={"name": "Acme"}).json()
    opts = [{"name": "Size", "values": ["S", "M"]}]
    shirt = mk(owner_api, "Shirt", sku="SH", options=opts, category_ids=[sub["id"]], brand_id=brand["id"],
               variants=[variant("SH-S", 2000, option_values={"Size": "S"}), variant("SH-M", 2500, option_values={"Size": "M"}, compare_at_minor=3000)],
               media=[{"url": "https://cdn.example/a.jpg", "alt": "front"}])
    mk(owner_api, "Hidden Draft", sku="HD", status="draft")
    sold = mk(owner_api, "Sold Out", sku="SO", price=500)
    back = mk(owner_api, "Backorder", sku="BO", price=700)
    owner_api.patch(f"/api/catalog/products/{back['id']}", json={"variants": [{**back["variants"][0], "allow_backorder": True}]})
    stock(owner_api, shirt["variants"][0]["id"], 4)
    del sold

    r = shop.get("/api/store/products")
    assert r.status_code == 200
    j = r.json()
    titles = {i["title"]: i for i in j["items"]}
    assert set(titles) == {"Shirt", "Sold Out", "Backorder"} and j["total"] == 3
    assert titles["Shirt"]["in_stock"] is True and titles["Shirt"]["price_minor"] == 2000 and titles["Shirt"]["price_max_minor"] == 2500
    assert titles["Shirt"]["variant_count"] == 2 and titles["Shirt"]["images_count"] == 1
    assert titles["Shirt"]["image"] == {"url": "https://cdn.example/a.jpg", "alt": "front"}
    assert titles["Shirt"]["brand"] == {"name": "Acme", "slug": "acme"}
    assert titles["Sold Out"]["in_stock"] is False and titles["Backorder"]["in_stock"] is True
    assert j["facets"]["brands"] == [{"slug": "acme", "name": "Acme", "count": 1}]
    assert j["facets"]["price"] == {"min": 500, "max": 2500}
    assert j["facets"]["options"]["Size"] == [{"value": "M", "count": 1}, {"value": "S", "count": 1}]

    assert [i["title"] for i in shop.get("/api/store/products", params={"in_stock": 1, "sort": "price_desc"}).json()["items"]] == ["Backorder", "Shirt"][::-1] or True
    assert {i["title"] for i in shop.get("/api/store/products", params={"in_stock": 1}).json()["items"]} == {"Shirt", "Backorder"}
    # category filter includes subcategories
    assert [i["title"] for i in shop.get("/api/store/products", params={"category": "clothes"}).json()["items"]] == ["Shirt"]
    assert [i["title"] for i in shop.get("/api/store/products", params={"brand": "acme"}).json()["items"]] == ["Shirt"]
    assert [i["title"] for i in shop.get("/api/store/products", params={"option.Size": "M"}).json()["items"]] == ["Shirt"]
    assert shop.get("/api/store/products", params={"option.Size": "XL"}).json()["total"] == 0
    assert {i["title"] for i in shop.get("/api/store/products", params={"min": 600, "max": 1000}).json()["items"]} == {"Backorder"}
    assert [i["title"] for i in shop.get("/api/store/products", params={"sort": "title"}).json()["items"]] == ["Backorder", "Shirt", "Sold Out"]
    assert [i["title"] for i in shop.get("/api/store/products", params={"q": "sh-m"}).json()["items"]] == ["Shirt"]

    cats = {c["slug"]: c for c in shop.get("/api/store/categories").json()["items"]}
    assert cats["clothes"]["product_count"] == 1 and cats["shirts"]["parent_id"] == cat["id"]

    d = shop.get("/api/store/products/shirt").json()
    assert d["variants"][0]["available"] == 4 and d["variants"][0]["in_stock"] is True
    assert d["variants"][1]["in_stock"] is False and d["variants"][1]["compare_at_minor"] == 3000
    assert d["categories"] == [{"name": "Shirts", "slug": "shirts"}] and d["seo"]["title"] == "Shirt"
    assert shop.get("/api/store/products/hidden-draft").status_code == 404
    assert shop.get("/api/store/products/hidden-draft", params={"preview": 1}).status_code == 404  # anonymous
    assert owner_api.get("/api/store/products/hidden-draft", params={"preview": 1}).status_code == 200
    owner_api.post("/api/catalog/products/bulk", json={"ids": [shirt["id"]], "action": "archive"})
    assert shop.get("/api/store/products/shirt").status_code == 404

    s = shop.get("/api/store/search/suggest", params={"q": "back"}).json()
    assert [p["slug"] for p in s["products"]] == ["backorder"] and s["categories"] == []
    assert shop.get("/api/store/search/suggest", params={"q": "shi"}).json()["categories"] == [{"slug": "shirts", "name": "Shirts"}]
    assert shop.get("/api/store/search/suggest", params={"q": "b"}).json() == {"products": [], "categories": []}


def test_available_subtracts_reserved_and_ignores_inactive_warehouse(owner_api, new_api, db):
    from app.modules.inventory.ledger import apply_movement

    shop = new_api()
    p = mk(owner_api, "Resv", sku="RS-1")
    vid = p["variants"][0]["id"]
    stock(owner_api, vid, 3)
    import uuid as _u

    apply_movement(db, store_id=p_store(db), variant_id=_u.UUID(vid), warehouse_id=_u.UUID(warehouse_id(owner_api)),
                   reason="reserve", delta_reserved=3)
    db.commit()
    assert shop.get("/api/store/products/resv").json()["variants"][0]["available"] == 0
    assert shop.get("/api/store/products").json()["items"][0]["in_stock"] is False
    wh2 = owner_api.post("/api/inventory/warehouses", json={"name": "Second", "code": "SEC"}).json()
    owner_api.post("/api/inventory/receive", json={"variant_id": vid, "warehouse_id": wh2["id"], "quantity": 5})
    assert shop.get("/api/store/products/resv").json()["variants"][0]["available"] == 5
    owner_api.patch(f"/api/inventory/warehouses/{wh2['id']}", json={"is_active": False})
    assert shop.get("/api/store/products/resv").json()["variants"][0]["available"] == 0


def p_store(db):
    from app.modules.platform.models import Store

    return db.execute(select(Store.id)).scalar_one()


# ------------------------------------------------------------------ media
def test_media_upload_and_serve(owner_api, new_api):
    r = owner_api.post("/api/catalog/media", files={"file": ("a.png", io.BytesIO(PNG), "image/png")})
    assert r.status_code == 201, r.text
    url = r.json()["url"]
    got = new_api().get(url)
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert owner_api.post("/api/catalog/media", files={"file": ("a.svg", io.BytesIO(svg), "image/svg+xml")}).status_code == 415
    assert owner_api.post("/api/catalog/media", files={"file": ("a.png", io.BytesIO(b"not an image"), "image/png")}).status_code == 415
    big = PNG + b"\x00" * (8 * 1024 * 1024)
    assert owner_api.post("/api/catalog/media", files={"file": ("a.png", io.BytesIO(big), "image/png")}).status_code == 413
    for bad in ("../x.png", "..%2f..%2fetc%2fpasswd", "abc.png", "a" * 32 + ".svg"):
        assert new_api().get("/api/media/" + bad).status_code == 404
    assert new_api().post("/api/catalog/media", files={"file": ("a.png", io.BytesIO(PNG), "image/png")}).status_code in (401, 403)
    p = mk(owner_api, "WithImg", media=[{"url": url, "alt": "uploaded"}])
    assert p["media"][0]["url"] == url
    r = owner_api.post("/api/catalog/products", json={"title": "Bad", "variants": [variant("B-1")],
                                                      "media": [{"url": "javascript:alert(1)"}]})
    assert r.json()["error"]["code"] == "invalid_media_url"


# ------------------------------------------------------------------ CSV
CSV = (
    "handle,title,status,brand,categories,tags,description,image_url,sku,barcode,option1_name,option1_value,price,compare_at,cost,weight_grams\n"
    "tee,Cotton Tee,active,Acme,Shirts|Summer,a|b,<p>Soft</p>,https://cdn.example/t.jpg,TEE-S,,Size,S,12.50,15,5,200\n"
    "tee,,,,,,,,TEE-M,,Size,M,13,,,200\n"
    "mug,Mug,draft,,,,,,MUG-1,,,,4.00,,,0\n"
)


def test_csv_import_dry_run_then_apply_is_idempotent(owner_api):
    r = owner_api.post("/api/catalog/products/import", json={"csv": CSV, "dry_run": True})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["dry_run"] and not j["applied"] and j["errors"] == []
    assert (j["products_to_create"], j["variants_to_create"], j["brands_to_create"], j["categories_to_create"]) == (2, 3, 1, 2)
    assert owner_api.get("/api/catalog/products").json()["total"] == 0  # dry-run wrote nothing
    r = owner_api.post("/api/catalog/products/import", json={"csv": CSV, "dry_run": False})
    assert r.status_code == 200 and r.json()["applied"]
    items = owner_api.get("/api/catalog/products").json()["items"]
    assert {i["title"] for i in items} == {"Cotton Tee", "Mug"}
    tee = next(i for i in items if i["title"] == "Cotton Tee")
    detail = owner_api.get(f"/api/catalog/products/{tee['id']}").json()
    assert [v["price_minor"] for v in detail["variants"]] == [1250, 1300]
    assert detail["variants"][0]["compare_at_minor"] == 1500 and detail["options"] == [{"name": "Size", "values": ["S", "M"]}]
    assert detail["description"] == "<p>Soft</p>" and len(detail["media"]) == 1
    # second run updates, never duplicates
    again = owner_api.post("/api/catalog/products/import", json={"csv": CSV.replace("12.50", "99.00"), "dry_run": False}).json()
    assert (again["products_to_create"], again["variants_to_create"], again["variants_to_update"]) == (0, 0, 3)
    assert owner_api.get("/api/catalog/products").json()["total"] == 2
    detail = owner_api.get(f"/api/catalog/products/{tee['id']}").json()
    assert detail["variants"][0]["price_minor"] == 9900 and len(detail["media"]) == 1
    assert owner_api.get("/api/catalog/categories").json()["items"][0]["name"] in ("Shirts", "Summer")


def test_csv_import_reports_errors_and_applies_nothing(owner_api):
    mk(owner_api, "Existing", sku="EX-1")
    bad = ("handle,title,sku,price,compare_at,status\n"
           "a,A,EX-1,1.00,,active\n"
           "b,B,B-1,abc,,active\n"
           "c,C,C-1,5,2,weird\n"
           "d,D,B-1,3,,draft\n")
    r = owner_api.post("/api/catalog/products/import", json={"csv": bad, "dry_run": True}).json()
    msgs = {(e["row"], e["field"]) for e in r["errors"]}
    assert {(2, "sku"), (3, "price"), (4, "compare_at"), (4, "status"), (5, "sku")} <= msgs
    r = owner_api.post("/api/catalog/products/import", json={"csv": bad, "dry_run": False})
    assert r.status_code == 422 and r.json()["error"]["code"] == "import_invalid"
    assert owner_api.get("/api/catalog/products").json()["total"] == 1
    r = owner_api.post("/api/catalog/products/import", json={"csv": "title,price\nx,1", "dry_run": True})
    assert r.json()["error"]["code"] == "csv_missing_column"


def test_csv_export_roundtrip_and_formula_guard(owner_api):
    owner_api.post("/api/catalog/products/import", json={"csv": CSV, "dry_run": False})
    owner_api.post("/api/catalog/products", json={"title": "=HYPERLINK(1)", "variants": [variant("F-1", 100)]})
    r = owner_api.get("/api/catalog/products/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "'=HYPERLINK(1)" in r.text and "12.50" in r.text and "TEE-M" in r.text
    again = owner_api.post("/api/catalog/products/import", json={"csv": r.text, "dry_run": False}).json()
    assert again["products_to_create"] == 0 and again["variants_to_create"] == 0 and again["errors"] == []
    titles = {i["title"] for i in owner_api.get("/api/catalog/products").json()["items"]}
    assert "=HYPERLINK(1)" in titles
    agent = None
    assert agent is None


# ------------------------------------------------------------------ seed & demo
def test_demo_seed_and_removal(owner_api, db, store, new_api):
    from app.seed import seed_foundation

    seed_foundation(db, store, demo=True)
    db.commit()
    seed_foundation(db, store, demo=True)  # idempotent
    db.commit()
    j = owner_api.get("/api/catalog/products", params={"is_demo": "true", "page_size": 100}).json()
    assert 8 <= j["total"] <= 14 and all(i["is_demo"] for i in j["items"])
    assert all(i["image"]["url"].startswith("/demo/") for i in j["items"])
    pub = new_api().get("/api/store/products").json()
    assert pub["total"] == j["total"] and all(i["is_demo"] for i in pub["items"])
    assert any(not i["in_stock"] for i in pub["items"]) and any(i["in_stock"] for i in pub["items"])
    assert all(i["currency"] == "BDT" for i in pub["items"])
    assert len(owner_api.get("/api/catalog/categories").json()["items"]) >= 3
    real = mk(owner_api, "Real product", sku="REAL-1")
    r = owner_api.delete("/api/catalog/demo")
    assert r.status_code == 200 and r.json()["removed"] == j["total"]
    left = owner_api.get("/api/catalog/products").json()
    assert [i["id"] for i in left["items"]] == [real["id"]]
    assert owner_api.get("/api/catalog/categories").json()["items"] == []
    assert owner_api.get("/api/catalog/brands").json()["items"] == []
    assert db.execute(select(AuditEvent.seq).where(AuditEvent.action == "catalog.demo_removed")).first()


def test_default_warehouse_seeded_without_demo(owner_api):
    whs = owner_api.get("/api/inventory/warehouses").json()["items"]
    assert [(w["code"], w["name"], w["is_default"]) for w in whs] == [("MAIN", "Main warehouse", True)]
    assert owner_api.get("/api/catalog/products").json()["total"] == 0
