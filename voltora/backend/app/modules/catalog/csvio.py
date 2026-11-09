"""CSV export and import (dry-run validation, then idempotent apply keyed by handle + SKU)."""
from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.deps import Principal
from ...core.errors import AppError
from ...core.services import audit, emit
from ...db import utcnow
from .models import Brand, Category, Product, ProductCategory, ProductMedia, Variant
from .products import (
    MAX_OPTIONS,
    MAX_VARIANTS,
    STATUSES,
    clean_tags,
    money_label_exp,
    safe_media_url,
    variant_title,
)
from .service import EV_PRODUCT_CREATED, EV_PRODUCT_UPDATED, sanitize_html, slugify, unique_slug

MAX_ROWS = 5000
MAX_BYTES = 5 * 1024 * 1024
OPT_COLS = [(f"option{i}_name", f"option{i}_value") for i in range(1, 4)]
HEADERS = ["handle", "title", "status", "brand", "categories", "tags", "description", "image_url", "sku", "barcode",
           *[c for pair in OPT_COLS for c in pair], "price", "compare_at", "cost", "weight_grams",
           "track_inventory", "allow_backorder"]
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s.startswith(_FORMULA) else s


def _unsafe_cell(s: str) -> str:
    return s[1:] if len(s) > 1 and s[0] == "'" and s[1] in "=+-@" else s


def fmt_money(minor: int | None, exp: int) -> str:
    if minor is None:
        return ""
    return f"{Decimal(minor).scaleb(-exp):.{exp}f}"


def parse_money(raw: str, exp: int) -> int | None:
    raw = raw.strip().replace(",", "")
    if not raw:
        return None
    try:
        d = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("not a number") from exc
    if d < 0:
        raise ValueError("must not be negative")
    scaled = d.scaleb(exp)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"too many decimal places (max {exp})")
    return int(scaled)


def _bool(raw: str, default: bool) -> bool:
    r = raw.strip().lower()
    if not r:
        return default
    if r in ("1", "true", "yes", "y"):
        return True
    if r in ("0", "false", "no", "n"):
        return False
    raise ValueError("use true or false")


def export_csv(db: Session, p: Principal) -> str:
    exp = money_label_exp(p.store.currency)
    products = db.execute(select(Product).where(Product.store_id == p.store.id)
                          .order_by(Product.created_at, Product.id)).scalars().all()
    brands = {b.id: b.name for b in db.execute(select(Brand).where(Brand.store_id == p.store.id)).scalars()}
    cats = {c.id: c.name for c in db.execute(select(Category).where(Category.store_id == p.store.id)).scalars()}
    pcats: dict = {}
    for pid, cid in db.execute(select(ProductCategory.product_id, ProductCategory.category_id)
                               .order_by(ProductCategory.position)):
        pcats.setdefault(pid, []).append(cats.get(cid, ""))
    images: dict = {}
    for m in db.execute(select(ProductMedia).order_by(ProductMedia.position)).scalars():
        images.setdefault(m.product_id, m.url)
    variants: dict = {}
    for v in db.execute(select(Variant).where(Variant.store_id == p.store.id)
                        .order_by(Variant.position, Variant.created_at)).scalars():
        variants.setdefault(v.product_id, []).append(v)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(HEADERS)
    for pr in products:
        for i, v in enumerate(variants.get(pr.id, [])):
            opts = [(o["name"], v.option_values.get(o["name"], "")) for o in (pr.options or [])][:3]
            opts += [("", "")] * (3 - len(opts))
            first = i == 0
            w.writerow([_safe_cell(x) for x in [
                pr.slug, pr.title if first else "", pr.status if first else "", brands.get(pr.brand_id, "") if first else "",
                "|".join(pcats.get(pr.id, [])) if first else "", "|".join(pr.tags or []) if first else "",
                pr.description if first else "", images.get(pr.id, "") if first else "", v.sku, v.barcode or "",
                *[c for pair in opts for c in pair], fmt_money(v.price_minor, exp), fmt_money(v.compare_at_minor, exp),
                fmt_money(v.cost_minor, exp), v.weight_grams, str(v.track_inventory).lower(),
                str(v.allow_backorder).lower()]])
    return out.getvalue()


# ------------------------------------------------------------------ import
def _parse(text: str, exp: int) -> tuple[dict[str, dict], list[dict], int]:
    if len(text.encode()) > MAX_BYTES:
        raise AppError("csv_too_large", "CSV is larger than 5 MB; split it into several files")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise AppError("csv_empty", "The CSV has no header row")
    reader.fieldnames = [(h or "").strip().lower() for h in reader.fieldnames]
    for req in ("sku", "price"):
        if req not in reader.fieldnames:
            raise AppError("csv_missing_column", f"Missing required column “{req}”")
    if "handle" not in reader.fieldnames and "title" not in reader.fieldnames:
        raise AppError("csv_missing_column", "Provide a “handle” or “title” column to group variants")
    errors: list[dict] = []
    groups: dict[str, dict] = {}
    n = 0
    last_handle = ""
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_ROWS:
            raise AppError("csv_too_many_rows", f"Import at most {MAX_ROWS} rows at a time")
        row = {k: _unsafe_cell((v or "").strip()) for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        raw_handle = row.get("handle") or row.get("title") or last_handle
        if not raw_handle:
            errors.append({"row": n, "field": "handle", "message": "Row has no handle or title"})
            continue
        last_handle = raw_handle
        handle = slugify(raw_handle, "product")
        g = groups.setdefault(handle, {"handle": handle, "first_row": n, "product": None, "variants": []})
        if g["product"] is None:
            g["product"] = row
            st = (row.get("status") or "").lower()
            if st and st not in STATUSES:
                errors.append({"row": n, "field": "status", "message": "Status must be draft, active or archived"})
        sku = row.get("sku", "")
        if not sku or len(sku) > 80:
            errors.append({"row": n, "field": "sku", "message": "SKU is required (max 80 characters)"})
            continue
        entry = {"row": n, "sku": sku, "barcode": row.get("barcode") or None, "options": {}}
        for nc, vc in OPT_COLS:
            if row.get(nc) and row.get(vc):
                entry["options"][row[nc]] = row[vc]
            elif row.get(nc) or row.get(vc):
                errors.append({"row": n, "field": nc, "message": "Option name and value must both be filled"})
        for fld, key in (("price", "price_minor"), ("compare_at", "compare_at_minor"), ("cost", "cost_minor")):
            try:
                entry[key] = parse_money(row.get(fld, ""), exp)
            except ValueError as e:
                errors.append({"row": n, "field": fld, "message": f"{fld}: {e}"})
        if entry.get("price_minor") is None and not any(e["row"] == n and e["field"] == "price" for e in errors):
            errors.append({"row": n, "field": "price", "message": "Price is required"})
        if entry.get("compare_at_minor") is not None and entry.get("price_minor") is not None \
                and entry["compare_at_minor"] < entry["price_minor"]:
            errors.append({"row": n, "field": "compare_at", "message": "Compare-at must not be lower than price"})
        try:
            entry["weight_grams"] = int(row.get("weight_grams") or 0)
            if entry["weight_grams"] < 0:
                raise ValueError
        except ValueError:
            errors.append({"row": n, "field": "weight_grams", "message": "Weight must be a whole number of grams"})
        for fld, default in (("track_inventory", True), ("allow_backorder", False)):
            try:
                entry[fld] = _bool(row.get(fld, ""), default)
            except ValueError as e:
                errors.append({"row": n, "field": fld, "message": str(e)})
        g["variants"].append(entry)
    return groups, errors, n


def run_import(db: Session, p: Principal, text: str, dry_run: bool) -> dict:
    store_id = p.store.id
    exp = money_label_exp(p.store.currency)
    groups, errors, _ = _parse(text, exp)
    seen_skus: dict[str, int] = {}
    existing_products = {pr.slug: pr for pr in db.execute(select(Product).where(
        Product.store_id == store_id, Product.slug.in_(list(groups)))).scalars()}
    all_skus = [v["sku"] for g in groups.values() for v in g["variants"]]
    sku_rows = {r.sku.lower(): r for r in db.execute(select(Variant).where(
        Variant.store_id == store_id, func.lower(Variant.sku).in_([s.lower() for s in all_skus]))).scalars()}
    report = {"products_to_create": 0, "products_to_update": 0, "variants_to_create": 0, "variants_to_update": 0,
              "brands_to_create": 0, "categories_to_create": 0}
    for g in groups.values():
        pr = existing_products.get(g["handle"])
        report["products_to_update" if pr else "products_to_create"] += 1
        if pr is None and not (g["product"].get("title")):
            errors.append({"row": g["first_row"], "field": "title", "message": "New products need a title"})
        names_order: list[str] = [o["name"] for o in (pr.options or [])] if pr else []
        for v in g["variants"]:
            low = v["sku"].lower()
            if low in seen_skus:
                errors.append({"row": v["row"], "field": "sku",
                               "message": f"SKU “{v['sku']}” already appears on row {seen_skus[low]}"})
                continue
            seen_skus[low] = v["row"]
            ex = sku_rows.get(low)
            if ex and (pr is None or ex.product_id != pr.id):
                errors.append({"row": v["row"], "field": "sku", "message": f"SKU “{v['sku']}” belongs to another product"})
            report["variants_to_update" if ex else "variants_to_create"] += 1
            for name in v["options"]:
                if name not in names_order:
                    names_order.append(name)
        if len(names_order) > MAX_OPTIONS:
            errors.append({"row": g["first_row"], "field": "options", "message": f"At most {MAX_OPTIONS} options"})
        if len(g["variants"]) > MAX_VARIANTS:
            errors.append({"row": g["first_row"], "field": "sku", "message": f"At most {MAX_VARIANTS} variants per product"})
        url = g["product"].get("image_url")
        if url:
            try:
                safe_media_url(url)
            except AppError as e:
                errors.append({"row": g["first_row"], "field": "image_url", "message": e.message})
    brand_names = {g["product"].get("brand") for g in groups.values() if g["product"].get("brand")}
    have_brands = {b.name.lower() for b in db.execute(select(Brand).where(Brand.store_id == store_id)).scalars()}
    report["brands_to_create"] = len({b for b in brand_names if b.lower() not in have_brands})
    cat_names = {c for g in groups.values() for c in g["product"].get("categories", "").split("|") if c.strip()}
    have_cats = {c.name.lower() for c in db.execute(select(Category).where(Category.store_id == store_id)).scalars()}
    report["categories_to_create"] = len({c.strip() for c in cat_names if c.strip().lower() not in have_cats})
    result = {"dry_run": dry_run, "rows": sum(len(g["variants"]) for g in groups.values()), **report,
              "errors": errors[:200], "error_count": len(errors), "applied": False}
    if errors:
        if not dry_run:
            raise AppError("import_invalid", f"{len(errors)} problem(s) found; nothing was imported", status=422,
                           details=result)
        return result
    if dry_run:
        return result
    _apply(db, p, groups, existing_products, sku_rows)
    audit(db, store_id, p.actor, "product.imported", "product", None, {k: v for k, v in report.items()})
    result["applied"] = True
    return result


def _apply(db: Session, p: Principal, groups: dict, existing: dict, sku_rows: dict) -> None:
    store_id = p.store.id
    brands = {b.name.lower(): b for b in db.execute(select(Brand).where(Brand.store_id == store_id)).scalars()}
    cats = {c.name.lower(): c for c in db.execute(select(Category).where(Category.store_id == store_id)).scalars()}
    for g in groups.values():
        row = g["product"]
        pr = existing.get(g["handle"])
        creating = pr is None
        if creating:
            pr = Product(store_id=store_id, title=row["title"], slug=unique_slug(db, Product, store_id, g["handle"], fallback="product"),
                         status=(row.get("status") or "draft").lower(), options=[], tags=[])
            db.add(pr)
            db.flush()
        else:
            if row.get("title"):
                pr.title = row["title"]
            if row.get("status"):
                pr.status = row["status"].lower()
        if row.get("description"):
            pr.description = sanitize_html(row["description"])
        if row.get("tags"):
            pr.tags = clean_tags(row["tags"].split("|"))
        if pr.status == "active" and pr.published_at is None:
            pr.published_at = utcnow()
        if row.get("brand"):
            b = brands.get(row["brand"].lower())
            if b is None:
                b = Brand(store_id=store_id, name=row["brand"], slug=unique_slug(db, Brand, store_id, row["brand"], fallback="brand"))
                db.add(b)
                db.flush()
                brands[b.name.lower()] = b
            pr.brand_id = b.id
        if row.get("categories"):
            names = [c.strip() for c in row["categories"].split("|") if c.strip()]
            ids = []
            for nm in names:
                c = cats.get(nm.lower())
                if c is None:
                    c = Category(store_id=store_id, name=nm, slug=unique_slug(db, Category, store_id, nm, fallback="category"))
                    db.add(c)
                    db.flush()
                    cats[nm.lower()] = c
                ids.append(c.id)
            db.query(ProductCategory).filter(ProductCategory.product_id == pr.id).delete()
            for pos, cid in enumerate(dict.fromkeys(ids)):
                db.add(ProductCategory(product_id=pr.id, category_id=cid, position=pos))
        options = [{"name": o["name"], "values": list(o["values"])} for o in (pr.options or [])]
        for v in g["variants"]:
            for name, val in v["options"].items():
                o = next((x for x in options if x["name"] == name), None)
                if o is None:
                    o = {"name": name, "values": []}
                    options.append(o)
                if val not in o["values"]:
                    o["values"].append(val)
        pr.options = options
        names = [o["name"] for o in options]
        pos0 = db.execute(select(func.count(Variant.id)).where(Variant.product_id == pr.id)).scalar_one()
        for i, v in enumerate(g["variants"]):
            ex = sku_rows.get(v["sku"].lower())
            fields = dict(barcode=v["barcode"], option_values=v["options"], price_minor=v["price_minor"],
                          compare_at_minor=v["compare_at_minor"], cost_minor=v["cost_minor"],
                          weight_grams=v["weight_grams"], track_inventory=v["track_inventory"],
                          allow_backorder=v["allow_backorder"], title=variant_title(v["options"], names))
            if ex:
                for k, val in fields.items():
                    setattr(ex, k, val)
            else:
                db.add(Variant(store_id=store_id, product_id=pr.id, sku=v["sku"], position=pos0 + i, **fields))
        url = row.get("image_url")
        if url:
            have = {m.url for m in db.execute(select(ProductMedia).where(ProductMedia.product_id == pr.id)).scalars()}
            if url not in have:
                n = db.execute(select(func.count(ProductMedia.id)).where(ProductMedia.product_id == pr.id)).scalar_one()
                db.add(ProductMedia(product_id=pr.id, url=safe_media_url(url), alt=pr.title, position=n))
        db.flush()
        emit(db, store_id, EV_PRODUCT_CREATED if creating else EV_PRODUCT_UPDATED,
             {"product_id": str(pr.id), "source": "csv_import"})

