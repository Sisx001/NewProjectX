"""Catalog admin API: products, variants, categories, brands, media, bulk actions, CSV, demo removal."""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.deps import Principal, parse_uuid, require
from ...core.errors import AppError, Conflict, NotFound
from ...core.services import Page, audit, emit
from ...db import get_db
from ..inventory.models import StockMovement
from . import csvio
from .models import Brand, Category, Product, ProductCategory, Variant
from .products import (
    STATUSES,
    MediaIn,
    OptionIn,
    ProductIn,
    VariantIn,
    generate_matrix,
    product_detail,
    save_product,
    validate_options,
)
from .service import (
    EV_PRODUCT_UPDATED,
    descendants,
    first_images,
    like,
    product_aggregates,
    unique_slug,
)
from .store_api import store_router

router = APIRouter(prefix="/api/catalog", tags=["catalog"])
media_router = APIRouter(prefix="/api", tags=["catalog"])
extra_routers = [store_router, media_router]
MODULE = {"title": "Catalog", "description": "Products, variants, categories, brands and media"}

MAX_UPLOAD = 8 * 1024 * 1024
_SIGS = [(b"\x89PNG\r\n\x1a\n", "png", "image/png"), (b"\xff\xd8\xff", "jpg", "image/jpeg")]
_MEDIA_RE = re.compile(r"^[0-9a-f]{32}\.(png|jpg|webp)$")
_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def _get_product(db: Session, p: Principal, product_id: str) -> Product:
    pid = parse_uuid(product_id, "Product")
    pr = db.execute(select(Product).where(Product.id == pid, Product.store_id == p.store.id)).scalar_one_or_none()
    if pr is None:
        raise NotFound("Product")
    return pr


# ------------------------------------------------------------------ products: list
SORTS = {"updated": Product.updated_at, "created": Product.created_at, "title": func.lower(Product.title)}


@router.get("/products")
def list_products(
    q: str = Query("", max_length=100), status: str | None = None, category_id: uuid.UUID | None = None,
    brand_id: uuid.UUID | None = None, stock: str | None = Query(None, pattern="^(in|low|out)$"),
    is_demo: bool | None = None, tag: str | None = Query(None, max_length=40),
    sort: str = Query("updated", pattern="^(updated|created|title|price|stock)$"),
    dir: str = Query("desc", pattern="^(asc|desc)$"), page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db), p: Principal = Depends(require("catalog.read")),
):
    sid = p.store.id
    agg = product_aggregates(sid, only_active_variants=False)
    base = select(Product, agg.c.pmin, agg.c.pmax, agg.c.vc, agg.c.instock, agg.c.low, agg.c.tracked) \
        .outerjoin(agg, agg.c.pid == Product.id).where(Product.store_id == sid)
    q = q.strip()
    if q:
        pat = like(q)
        sku_hit = select(Variant.product_id).where(Variant.store_id == sid, or_(Variant.sku.ilike(pat), Variant.barcode == q))
        base = base.where(or_(Product.title.ilike(pat), cast(Product.tags, String).ilike(pat), Product.id.in_(sku_hit)))
    if status:
        if status not in STATUSES:
            raise AppError("invalid_status", "Unknown status filter")
        base = base.where(Product.status == status)
    if category_id:
        cats = db.execute(select(Category).where(Category.store_id == sid)).scalars().all()
        ids = descendants(list(cats), {category_id})
        base = base.where(Product.id.in_(select(ProductCategory.product_id).where(ProductCategory.category_id.in_(ids))))
    if brand_id:
        base = base.where(Product.brand_id == brand_id)
    if is_demo is not None:
        base = base.where(Product.is_demo.is_(is_demo))
    if tag:
        base = base.where(Product.tags.contains([tag]))
    if stock == "out":
        base = base.where(func.coalesce(agg.c.instock, False).is_(False))
    elif stock == "in":
        base = base.where(agg.c.instock.is_(True))
    elif stock == "low":
        base = base.where(agg.c.low.is_(True))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    cols = {**SORTS, "price": func.coalesce(agg.c.pmin, 0), "stock": func.coalesce(agg.c.tracked, 0)}
    col = cols[sort]
    order = col.asc() if dir == "asc" else col.desc()
    rows = db.execute(base.order_by(order, Product.id).limit(page_size).offset((page - 1) * page_size)).all()
    pids = [r[0].id for r in rows]
    media = first_images(db, pids)
    brands = {b.id: b.name for b in db.execute(select(Brand).where(Brand.store_id == sid)).scalars()}
    cat_names: dict = {}
    for pid, name in db.execute(select(ProductCategory.product_id, Category.name).join(
            Category, Category.id == ProductCategory.category_id).where(ProductCategory.product_id.in_(pids))):
        cat_names.setdefault(pid, []).append(name)
    items = []
    for pr, pmin, pmax, vc, instock, low, tracked in rows:
        imgs = media.get(pr.id, [])
        items.append({
            "id": str(pr.id), "title": pr.title, "slug": pr.slug, "status": pr.status, "is_demo": pr.is_demo,
            "image": {"url": imgs[0].url, "alt": imgs[0].alt} if imgs else None, "variant_count": vc or 0,
            "price_min_minor": pmin, "price_max_minor": pmax, "currency": p.store.currency,
            "stock_state": "low" if low else ("in" if instock else "out"), "tracked_available": int(tracked or 0),
            "brand": brands.get(pr.brand_id), "categories": cat_names.get(pr.id, []), "tags": pr.tags or [],
            "updated_at": pr.updated_at.isoformat()})
    return Page(items, total, page, page_size).dump()


# ------------------------------------------------------------------ products: fixed-path routes first
class GenerateIn(BaseModel):
    options: list[OptionIn]
    price_minor: int = Field(default=0, ge=0)
    sku_prefix: str = Field(default="", max_length=40)
    existing: list[VariantIn] = Field(default_factory=list)


@router.post("/products/generate-variants")
def generate_variants(body: GenerateIn, p: Principal = Depends(require("catalog.write"))):
    options = validate_options(body.options)
    keep = {tuple(sorted(v.option_values.items())): v for v in body.existing}
    out = []
    for combo in generate_matrix(options):
        ex = keep.get(tuple(sorted(combo.items())))
        if ex:
            out.append(ex.model_dump(mode="json"))
            continue
        suffix = "-".join(re.sub(r"[^A-Za-z0-9]+", "", v).upper()[:6] for v in combo.values())
        sku = "-".join(x for x in [body.sku_prefix.strip().upper(), suffix] if x)
        out.append({"id": None, "sku": sku, "barcode": None, "title": " / ".join(combo.values()) or "Default",
                    "option_values": combo, "price_minor": body.price_minor, "compare_at_minor": None,
                    "cost_minor": None, "weight_grams": 0, "track_inventory": True, "allow_backorder": False,
                    "is_active": True})
    return {"variants": out, "count": len(out)}


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    action: str = Field(pattern="^(set_status|add_tag|remove_tag|archive)$")
    value: str | None = Field(default=None, max_length=40)


@router.post("/products/bulk")
def bulk_products(body: BulkIn, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.write"))):
    rows = db.execute(select(Product).where(Product.store_id == p.store.id, Product.id.in_(body.ids))).scalars().all()
    if len(rows) != len(set(body.ids)):
        raise NotFound("One or more products")
    action, value = body.action, (body.value or "").strip()
    if action == "set_status" and value not in STATUSES:
        raise AppError("invalid_status", "Status must be draft, active or archived")
    if action in ("add_tag", "remove_tag") and not value:
        raise AppError("tag_required", "Enter a tag")
    done = 0
    for pr in rows:
        if action in ("set_status", "archive"):
            new = "archived" if action == "archive" else value
            if new == "active" and not db.execute(select(Variant.id).where(
                    Variant.product_id == pr.id, Variant.is_active.is_(True)).limit(1)).first():
                raise AppError("no_active_variant", f"“{pr.title}” has no active variant and cannot be published")
            if pr.status != new:
                pr.status = new
                if new == "active" and pr.published_at is None:
                    from ...db import utcnow
                    pr.published_at = utcnow()
                done += 1
        else:
            tags = list(pr.tags or [])
            has = value.lower() in {t.lower() for t in tags}
            if action == "add_tag" and not has:
                pr.tags = [*tags, value]
                done += 1
            elif action == "remove_tag" and has:
                pr.tags = [t for t in tags if t.lower() != value.lower()]
                done += 1
        emit(db, p.store.id, EV_PRODUCT_UPDATED, {"product_id": str(pr.id), "fields": [action]})
    audit(db, p.store.id, p.actor, "product.bulk_" + action, "product", None,
          {"count": len(rows), "changed": done, "value": value or None, "ids": [str(i) for i in body.ids][:100]})
    db.commit()
    return {"selected": len(rows), "updated": done}


@router.get("/products/export.csv")
def export_products(db: Session = Depends(get_db), p: Principal = Depends(require("catalog.read"))):
    audit(db, p.store.id, p.actor, "product.exported", "product", None, {})
    db.commit()
    return Response(csvio.export_csv(db, p), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="voltora-products.csv"'})


class ImportIn(BaseModel):
    csv: str = Field(max_length=6_000_000)
    dry_run: bool = True


@router.post("/products/import")
def import_products(body: ImportIn, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.write"))):
    try:
        result = csvio.run_import(db, p, body.csv, body.dry_run)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("import_conflict", "A SKU or handle collided with a concurrent change; retry") from exc
    return result


@router.post("/products", status_code=201)
def create_product(body: ProductIn, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.write"))):
    try:
        pr = save_product(db, p, body)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("conflict", "That slug or SKU is already in use") from exc
    return product_detail(db, p, pr)


@router.get("/products/{product_id}")
def get_product(product_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.read"))):
    return product_detail(db, p, _get_product(db, p, product_id))


@router.patch("/products/{product_id}")
def update_product(product_id: str, body: ProductIn, db: Session = Depends(get_db),
                   p: Principal = Depends(require("catalog.write"))):
    pr = _get_product(db, p, product_id)
    try:
        save_product(db, p, body, pr)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("conflict", "That slug or SKU is already in use") from exc
    db.refresh(pr)
    return product_detail(db, p, pr)


@router.delete("/products/{product_id}")
def delete_product(product_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.write"))):
    pr = _get_product(db, p, product_id)
    moved = db.execute(select(StockMovement.id).join(Variant, Variant.id == StockMovement.variant_id)
                       .where(Variant.product_id == pr.id, StockMovement.reason != "receive").limit(1)).first()
    if moved:
        raise Conflict("product_in_use", "This product has stock history. Archive it instead.")
    title = pr.title
    try:
        with db.begin_nested():
            db.delete(pr)
            db.flush()
    except IntegrityError as exc:
        raise Conflict("product_in_use", "This product is referenced by orders or other records. Archive it instead.") from exc
    audit(db, p.store.id, p.actor, "product.deleted", "product", product_id, {"title": title})
    db.commit()
    return {"ok": True}


@router.post("/products/{product_id}/duplicate", status_code=201)
def duplicate_product(product_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.write"))):
    src = _get_product(db, p, product_id)
    detail = product_detail(db, p, src)
    taken = {r[0].lower() for r in db.execute(select(Variant.sku).where(Variant.store_id == p.store.id))}
    variants = []
    id_map: dict = {}
    for v in detail["variants"]:
        sku, n = f"{v['sku']}-COPY"[:80], 1
        while sku.lower() in taken:
            n += 1
            sku = f"{v['sku']}-COPY{n}"[:80]
        taken.add(sku.lower())
        variants.append(VariantIn(sku=sku, barcode=None, title=v["title"], option_values=v["option_values"],
                                  price_minor=v["price_minor"], compare_at_minor=v["compare_at_minor"],
                                  cost_minor=v["cost_minor"], weight_grams=v["weight_grams"],
                                  track_inventory=v["track_inventory"], allow_backorder=v["allow_backorder"],
                                  is_active=v["is_active"]))
        id_map[v["id"]] = len(variants) - 1
    body = ProductIn(title=f"{src.title} (copy)"[:300], description=src.description, status="draft",
                     product_type=src.product_type, brand_id=src.brand_id, tags=list(src.tags or []),
                     options=[OptionIn(**o) for o in src.options or []], attributes=dict(src.attributes or {}),
                     seo=detail["seo"], tax_class=src.tax_class,
                     category_ids=[uuid.UUID(c) for c in detail["category_ids"]],
                     media=[MediaIn(url=m["url"], alt=m["alt"]) for m in detail["media"]], variants=variants)
    pr = save_product(db, p, body, audit_action="product.duplicated")
    db.commit()
    return product_detail(db, p, pr)


# ------------------------------------------------------------------ categories
class CategoryIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(default=None, max_length=160)
    parent_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=5000)
    image_url: str | None = Field(default=None, max_length=500)
    position: int | None = None
    is_active: bool | None = None
    seo: dict | None = None


def category_out(c: Category, count: int = 0) -> dict:
    return {"id": str(c.id), "name": c.name, "slug": c.slug, "parent_id": str(c.parent_id) if c.parent_id else None,
            "description": c.description, "image_url": c.image_url, "position": c.position,
            "is_active": c.is_active, "seo": c.seo or {}, "is_demo": c.is_demo, "product_count": count}


@router.get("/categories")
def list_categories(db: Session = Depends(get_db), p: Principal = Depends(require("catalog.read"))):
    counts = dict(db.execute(select(ProductCategory.category_id, func.count()).join(
        Product, Product.id == ProductCategory.product_id).where(Product.store_id == p.store.id)
        .group_by(ProductCategory.category_id)).all())
    cats = db.execute(select(Category).where(Category.store_id == p.store.id)
                      .order_by(Category.position, Category.name)).scalars().all()
    return {"items": [category_out(c, counts.get(c.id, 0)) for c in cats]}


def _check_parent(db: Session, p: Principal, cat: Category | None, parent_id: uuid.UUID | None) -> None:
    if parent_id is None:
        return
    parent = db.execute(select(Category).where(Category.id == parent_id, Category.store_id == p.store.id)).scalar_one_or_none()
    if parent is None:
        raise AppError("invalid_parent", "Parent category does not exist")
    if cat is not None:
        cats = db.execute(select(Category).where(Category.store_id == p.store.id)).scalars().all()
        if parent_id in descendants(list(cats), {cat.id}):
            raise AppError("category_cycle", "A category cannot be moved inside itself or one of its subcategories")


@router.post("/categories", status_code=201)
def create_category(body: CategoryIn, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.write"))):
    if not body.name:
        raise AppError("name_required", "Category name is required")
    _check_parent(db, p, None, body.parent_id)
    c = Category(store_id=p.store.id, name=body.name.strip(), parent_id=body.parent_id,
                 slug=unique_slug(db, Category, p.store.id, body.slug or body.name, fallback="category"),
                 description=body.description or "", image_url=body.image_url or None, position=body.position or 0,
                 is_active=True if body.is_active is None else body.is_active, seo=body.seo or {})
    db.add(c)
    db.flush()
    audit(db, p.store.id, p.actor, "category.created", "category", c.id, {"name": c.name})
    db.commit()
    return category_out(c)


@router.patch("/categories/{category_id}")
def update_category(category_id: str, body: CategoryIn, db: Session = Depends(get_db),
                    p: Principal = Depends(require("catalog.write"))):
    cid = parse_uuid(category_id, "Category")
    c = db.execute(select(Category).where(Category.id == cid, Category.store_id == p.store.id)).scalar_one_or_none()
    if c is None:
        raise NotFound("Category")
    f = body.model_fields_set
    if "parent_id" in f:
        _check_parent(db, p, c, body.parent_id)
        c.parent_id = body.parent_id
    if "name" in f and body.name:
        c.name = body.name.strip()
    if "slug" in f and body.slug:
        c.slug = unique_slug(db, Category, p.store.id, body.slug, exclude_id=c.id, fallback="category")
    for k in ("description", "image_url", "position", "is_active", "seo"):
        if k in f and getattr(body, k) is not None:
            setattr(c, k, getattr(body, k))
    if "image_url" in f and not body.image_url:
        c.image_url = None
    audit(db, p.store.id, p.actor, "category.updated", "category", c.id, {"fields": sorted(f)})
    db.commit()
    return category_out(c)


@router.delete("/categories/{category_id}")
def delete_category(category_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.write"))):
    cid = parse_uuid(category_id, "Category")
    c = db.execute(select(Category).where(Category.id == cid, Category.store_id == p.store.id)).scalar_one_or_none()
    if c is None:
        raise NotFound("Category")
    for child in db.execute(select(Category).where(Category.parent_id == c.id)).scalars():
        child.parent_id = c.parent_id
    name = c.name
    db.delete(c)
    audit(db, p.store.id, p.actor, "category.deleted", "category", cid, {"name": name})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ brands
class BrandIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(default=None, max_length=160)
    logo_url: str | None = Field(default=None, max_length=500)


def brand_out(b: Brand, count: int = 0) -> dict:
    return {"id": str(b.id), "name": b.name, "slug": b.slug, "logo_url": b.logo_url, "is_demo": b.is_demo,
            "product_count": count}


@router.get("/brands")
def list_brands(db: Session = Depends(get_db), p: Principal = Depends(require("catalog.read"))):
    counts = dict(db.execute(select(Product.brand_id, func.count()).where(
        Product.store_id == p.store.id, Product.brand_id.is_not(None)).group_by(Product.brand_id)).all())
    rows = db.execute(select(Brand).where(Brand.store_id == p.store.id).order_by(Brand.name)).scalars().all()
    return {"items": [brand_out(b, counts.get(b.id, 0)) for b in rows]}


@router.post("/brands", status_code=201)
def create_brand(body: BrandIn, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.write"))):
    if not body.name:
        raise AppError("name_required", "Brand name is required")
    b = Brand(store_id=p.store.id, name=body.name.strip(), logo_url=body.logo_url or None,
              slug=unique_slug(db, Brand, p.store.id, body.slug or body.name, fallback="brand"))
    db.add(b)
    db.flush()
    audit(db, p.store.id, p.actor, "brand.created", "brand", b.id, {"name": b.name})
    db.commit()
    return brand_out(b)


@router.patch("/brands/{brand_id}")
def update_brand(brand_id: str, body: BrandIn, db: Session = Depends(get_db),
                 p: Principal = Depends(require("catalog.write"))):
    bid = parse_uuid(brand_id, "Brand")
    b = db.execute(select(Brand).where(Brand.id == bid, Brand.store_id == p.store.id)).scalar_one_or_none()
    if b is None:
        raise NotFound("Brand")
    if body.name:
        b.name = body.name.strip()
    if body.slug:
        b.slug = unique_slug(db, Brand, p.store.id, body.slug, exclude_id=b.id, fallback="brand")
    if "logo_url" in body.model_fields_set:
        b.logo_url = body.logo_url or None
    audit(db, p.store.id, p.actor, "brand.updated", "brand", b.id, {"fields": sorted(body.model_fields_set)})
    db.commit()
    return brand_out(b)


@router.delete("/brands/{brand_id}")
def delete_brand(brand_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("catalog.write"))):
    bid = parse_uuid(brand_id, "Brand")
    b = db.execute(select(Brand).where(Brand.id == bid, Brand.store_id == p.store.id)).scalar_one_or_none()
    if b is None:
        raise NotFound("Brand")
    name = b.name
    db.delete(b)
    audit(db, p.store.id, p.actor, "brand.deleted", "brand", bid, {"name": name})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ media
def _detect(data: bytes) -> tuple[str, str] | None:
    for sig, ext, mime in _SIGS:
        if data.startswith(sig):
            return ext, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


@router.post("/media", status_code=201)
async def upload_media(file: UploadFile = File(...), db: Session = Depends(get_db),
                       p: Principal = Depends(require("catalog.write"))):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise AppError("file_too_large", "Images must be 8 MB or smaller", status=413)
    kind = _detect(data)
    if kind is None:
        raise AppError("unsupported_media", "Upload a PNG, JPEG or WebP image (SVG uploads are not accepted)", status=415)
    ext, mime = kind
    folder = get_settings().data_dir / "media"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    tmp = folder / (name + ".part")
    tmp.write_bytes(data)
    tmp.replace(folder / name)
    audit(db, p.store.id, p.actor, "media.uploaded", "media", name, {"bytes": len(data), "type": mime})
    db.commit()
    return {"url": f"/api/media/{name}", "size": len(data), "content_type": mime}


@media_router.get("/media/{filename}")
def serve_media(filename: str):
    if not _MEDIA_RE.match(filename):
        raise NotFound("File")
    folder = (get_settings().data_dir / "media").resolve()
    path = (folder / filename).resolve()
    if path.parent != folder or not path.is_file():
        raise NotFound("File")
    return FileResponse(path, media_type=_MEDIA_TYPES[filename.rsplit(".", 1)[1]], headers={
        "Cache-Control": "public, max-age=86400, immutable", "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------ demo data
@router.delete("/demo")
def remove_demo(db: Session = Depends(get_db),
                p: Principal = Depends(require("catalog.write"))):
    sid = p.store.id
    ids = [r[0] for r in db.execute(select(Product.id).where(Product.store_id == sid, Product.is_demo.is_(True)))]
    blocked = 0
    removed = 0
    for pid in ids:
        pr = db.get(Product, pid)
        try:
            with db.begin_nested():
                db.execute(delete(StockMovement).where(StockMovement.variant_id.in_(
                    select(Variant.id).where(Variant.product_id == pid))))
                db.delete(pr)
                db.flush()
            removed += 1
        except IntegrityError:
            pr.status = "archived"  # referenced by real orders: keep the record
            blocked += 1
    used_cats = select(ProductCategory.category_id)
    db.execute(delete(Category).where(Category.store_id == sid, Category.is_demo.is_(True),
                                      Category.id.not_in(used_cats)))
    db.execute(delete(Brand).where(Brand.store_id == sid, Brand.is_demo.is_(True),
                                   Brand.id.not_in(select(Product.brand_id).where(Product.brand_id.is_not(None)))))
    audit(db, sid, p.actor, "catalog.demo_removed", "product", None, {"removed": removed, "archived": blocked})
    db.commit()
    return {"removed": removed, "archived_because_in_use": blocked}
