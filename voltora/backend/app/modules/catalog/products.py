"""Product aggregate: validation + persistence shared by the admin API, duplication, CSV import and seed."""
from __future__ import annotations

import itertools
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.deps import Principal
from ...core.errors import AppError, Conflict, NotFound
from ...core.services import audit, emit
from ...db import utcnow
from ..inventory.models import StockLevel, StockMovement, Warehouse
from .models import Brand, Category, Product, ProductCategory, ProductMedia, Variant
from .service import (
    EV_PRODUCT_CREATED,
    EV_PRODUCT_UPDATED,
    availability,
    sanitize_html,
    unique_slug,
)

STATUSES = ("draft", "active", "archived")
MAX_VARIANTS = 250
MAX_OPTIONS = 5
MAX_MEDIA = 50


class OptionIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    values: list[str] = Field(min_length=1, max_length=100)


class VariantIn(BaseModel):
    id: uuid.UUID | None = None
    sku: str = Field(min_length=1, max_length=80)
    barcode: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, max_length=200)
    option_values: dict[str, str] = Field(default_factory=dict)
    price_minor: int = Field(ge=0, le=10**12)
    compare_at_minor: int | None = Field(default=None, ge=0, le=10**12)
    cost_minor: int | None = Field(default=None, ge=0, le=10**12)
    weight_grams: int = Field(default=0, ge=0, le=10**7)
    track_inventory: bool = True
    allow_backorder: bool = False
    is_active: bool = True


class MediaIn(BaseModel):
    id: uuid.UUID | None = None
    url: str = Field(min_length=1, max_length=800)
    alt: str = Field(default="", max_length=300)
    variant_ids: list[uuid.UUID] = Field(default_factory=list)


class SeoIn(BaseModel):
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=320)


class ProductIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    slug: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=60000)
    status: str | None = None
    product_type: str | None = None
    brand_id: uuid.UUID | None = None
    tags: list[str] | None = None
    options: list[OptionIn] | None = None
    attributes: dict[str, Any] | None = None
    seo: SeoIn | None = None
    tax_class: str | None = Field(default=None, max_length=40)
    category_ids: list[uuid.UUID] | None = None
    media: list[MediaIn] | None = None
    variants: list[VariantIn] | None = None


def clean_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = " ".join(str(t).split())[:40]
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:50]


def safe_media_url(url: str) -> str:
    u = url.strip()
    if u.startswith(("/api/media/", "/demo/", "https://", "http://")):
        return u
    raise AppError("invalid_media_url", "Image URLs must be https://, http:// or an uploaded /api/media/ file")


def variant_title(values: dict[str, str], option_names: list[str]) -> str:
    parts = [values[n] for n in option_names if values.get(n)]
    return " / ".join(parts) or "Default"


def validate_options(options: list[OptionIn]) -> list[dict]:
    if len(options) > MAX_OPTIONS:
        raise AppError("too_many_options", f"A product supports up to {MAX_OPTIONS} options")
    seen: set[str] = set()
    out = []
    for o in options:
        name = o.name.strip()
        if name.lower() in seen:
            raise AppError("duplicate_option", f"Option “{name}” is listed twice")
        seen.add(name.lower())
        vals: list[str] = []
        for v in o.values:
            v = v.strip()
            if v and v not in vals:
                vals.append(v)
        if not vals:
            raise AppError("empty_option", f"Option “{name}” needs at least one value")
        out.append({"name": name, "values": vals})
    return out


def generate_matrix(options: list[dict]) -> list[dict[str, str]]:
    if not options:
        return [{}]
    total = 1
    for o in options:
        total *= len(o["values"])
    if total > MAX_VARIANTS:
        raise AppError("too_many_variants", f"That would create {total} variants; the limit is {MAX_VARIANTS} per product")
    names = [o["name"] for o in options]
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*[o["values"] for o in options])]


def _check_variants(db: Session, store_id, product_id, options: list[dict], variants: list[VariantIn]) -> None:
    if len(variants) > MAX_VARIANTS:
        raise AppError("too_many_variants", f"A product supports up to {MAX_VARIANTS} variants")
    names = [o["name"] for o in options]
    allowed = {o["name"]: set(o["values"]) for o in options}
    skus: set[str] = set()
    combos: set[tuple] = set()
    for i, v in enumerate(variants):
        sku = v.sku.strip()
        if sku.lower() in skus:
            raise Conflict("sku_duplicate", f"SKU “{sku}” is used twice on this product", {"index": i})
        skus.add(sku.lower())
        for k, val in v.option_values.items():
            if k not in allowed or val not in allowed[k]:
                raise AppError("invalid_option_value", f"Variant “{sku}” uses {k}={val}, which is not a defined option value")
        if names and set(v.option_values) != set(names):
            raise AppError("missing_option_value", f"Variant “{sku}” needs a value for every option")
        combo = tuple(v.option_values.get(n) for n in names)
        if combo in combos:
            raise Conflict("variant_duplicate", f"Two variants share the same option combination ({sku})")
        combos.add(combo)
        if v.compare_at_minor is not None and v.compare_at_minor < v.price_minor:
            raise AppError("invalid_compare_at", f"Variant “{sku}”: compare-at price must not be lower than the price")
    for i, v in enumerate(variants):
        q = select(Variant.id).where(Variant.store_id == store_id, func.lower(Variant.sku) == v.sku.strip().lower())
        if v.id:
            q = q.where(Variant.id != v.id)
        hit = db.execute(q.limit(1)).first()
        if hit:
            raise Conflict("sku_taken", f"SKU “{v.sku.strip()}” is already used by another variant", {"index": i})


def _sync_variants(db: Session, p: Principal, product: Product, variants: list[VariantIn]) -> None:
    store_id = p.store.id
    existing = {v.id: v for v in db.execute(select(Variant).where(Variant.product_id == product.id)).scalars()}
    names = [o["name"] for o in product.options]
    keep: set = set()
    for pos, vin in enumerate(variants):
        data = dict(sku=vin.sku.strip(), barcode=(vin.barcode or "").strip() or None,
                    option_values=dict(vin.option_values), price_minor=vin.price_minor,
                    compare_at_minor=vin.compare_at_minor, cost_minor=vin.cost_minor, weight_grams=vin.weight_grams,
                    track_inventory=vin.track_inventory, allow_backorder=vin.allow_backorder,
                    is_active=vin.is_active, position=pos,
                    title=(vin.title or "").strip() or variant_title(vin.option_values, names))
        row = existing.get(vin.id) if vin.id else None
        if vin.id and row is None:
            raise NotFound("Variant")
        if row is None:
            row = Variant(store_id=store_id, product_id=product.id, **data)
            db.add(row)
            db.flush()
        else:
            for k, val in data.items():
                setattr(row, k, val)
        keep.add(row.id)
    for vid, row in existing.items():
        if vid in keep:
            continue
        used = db.execute(select(StockMovement.id).where(StockMovement.variant_id == vid).limit(1)).first()
        if used:
            row.is_active = False  # history must stay referentially intact
            continue
        try:
            with db.begin_nested():
                db.delete(row)
                db.flush()
        except IntegrityError:
            row.is_active = False
    db.flush()


def _sync_media(db: Session, product: Product, media: list[MediaIn]) -> None:
    if len(media) > MAX_MEDIA:
        raise AppError("too_many_images", f"A product supports up to {MAX_MEDIA} images")
    valid_variants = {r[0] for r in db.execute(select(Variant.id).where(Variant.product_id == product.id)).all()}
    existing = {m.id: m for m in db.execute(select(ProductMedia).where(ProductMedia.product_id == product.id)).scalars()}
    keep = set()
    for pos, m in enumerate(media):
        url = safe_media_url(m.url)
        vids = [str(v) for v in m.variant_ids if v in valid_variants]
        row = existing.get(m.id) if m.id else None
        if row is None:
            row = ProductMedia(product_id=product.id, url=url, alt=m.alt.strip(), position=pos, variant_ids=vids)
            db.add(row)
            db.flush()
        else:
            row.url, row.alt, row.position, row.variant_ids = url, m.alt.strip(), pos, vids
        keep.add(row.id)
    for mid in set(existing) - keep:
        db.delete(existing[mid])


def _sync_categories(db: Session, store_id, product: Product, ids: list[uuid.UUID]) -> None:
    ids = list(dict.fromkeys(ids))
    found = {r[0] for r in db.execute(select(Category.id).where(Category.store_id == store_id, Category.id.in_(ids)))}
    if found != set(ids):
        raise AppError("invalid_category", "One or more categories do not exist")
    db.execute(delete(ProductCategory).where(ProductCategory.product_id == product.id))
    for pos, cid in enumerate(ids):
        db.add(ProductCategory(product_id=product.id, category_id=cid, position=pos))


def save_product(db: Session, p: Principal, body: ProductIn, product: Product | None = None, *, is_demo: bool = False,
                 audit_action: str | None = None) -> Product:
    """Create (product is None) or patch a product and its variants/media/categories in the caller's transaction."""
    store_id = p.store.id
    fields = body.model_fields_set
    creating = product is None
    if creating:
        if not body.title:
            raise AppError("title_required", "A product needs a title")
        slug = unique_slug(db, Product, store_id, body.slug or body.title, fallback="product")
        product = Product(store_id=store_id, title=body.title.strip(), slug=slug, is_demo=is_demo)
        db.add(product)
    changed: list[str] = []

    def touch(name: str, value):
        if creating or getattr(product, name) != value:
            changed.append(name)
        setattr(product, name, value)

    if "title" in fields and body.title is not None and not creating:
        touch("title", body.title.strip())
    if "description" in fields and body.description is not None:
        touch("description", sanitize_html(body.description))
    if "status" in fields and body.status is not None:
        if body.status not in STATUSES:
            raise AppError("invalid_status", "Status must be draft, active or archived")
        touch("status", body.status)
        if body.status == "active" and product.published_at is None:
            product.published_at = utcnow()
    if "product_type" in fields and body.product_type is not None:
        if body.product_type not in ("physical", "digital", "service"):
            raise AppError("invalid_product_type", "Product type must be physical, digital or service")
        touch("product_type", body.product_type)
    if "brand_id" in fields:
        if body.brand_id is not None and not db.execute(select(Brand.id).where(Brand.id == body.brand_id,
                                                                               Brand.store_id == store_id)).first():
            raise AppError("invalid_brand", "Brand does not exist")
        touch("brand_id", body.brand_id)
    if "tags" in fields and body.tags is not None:
        touch("tags", clean_tags(body.tags))
    if "attributes" in fields and body.attributes is not None:
        if len(str(body.attributes)) > 20000:
            raise AppError("attributes_too_large", "Custom attributes are too large")
        touch("attributes", body.attributes)
    if "seo" in fields and body.seo is not None:
        touch("seo", body.seo.model_dump())
    if "tax_class" in fields and body.tax_class:
        touch("tax_class", body.tax_class)
    if "options" in fields and body.options is not None:
        touch("options", validate_options(body.options))
    elif creating:
        product.options = []

    if not creating and "slug" in fields and body.slug:
        new = unique_slug(db, Product, store_id, body.slug, exclude_id=product.id, fallback="product")
        if new != product.slug:
            touch("slug", new)
    if creating:
        db.flush()

    if "variants" in fields and body.variants is not None:
        if not body.variants:
            raise AppError("variants_required", "A product needs at least one variant")
        _check_variants(db, store_id, product.id, product.options, body.variants)
        try:
            with db.begin_nested():
                _sync_variants(db, p, product, body.variants)
        except IntegrityError as exc:
            raise Conflict("sku_taken", "A SKU on this product is already in use") from exc
        changed.append("variants")
    elif creating:
        raise AppError("variants_required", "A product needs at least one variant")
    if "category_ids" in fields and body.category_ids is not None:
        _sync_categories(db, store_id, product, body.category_ids)
        changed.append("categories")
    if "media" in fields and body.media is not None:
        _sync_media(db, product, body.media)
        changed.append("media")

    if product.status == "active":
        active = db.execute(select(func.count(Variant.id)).where(Variant.product_id == product.id,
                                                                 Variant.is_active.is_(True))).scalar_one()
        if not active:
            raise AppError("no_active_variant", "An active product needs at least one active variant")
    db.flush()
    if creating:
        audit(db, store_id, p.actor, audit_action or "product.created", "product", product.id,
              {"title": product.title, "status": product.status})
        emit(db, store_id, EV_PRODUCT_CREATED, {"product_id": str(product.id), "title": product.title,
                                                "status": product.status})
    elif changed:
        audit(db, store_id, p.actor, audit_action or "product.updated", "product", product.id,
              {"fields": sorted(set(changed))})
        emit(db, store_id, EV_PRODUCT_UPDATED, {"product_id": str(product.id), "fields": sorted(set(changed))})
    return product


# ------------------------------------------------------------------ serialisation
def money_label_exp(currency: str) -> int:
    return {"JPY": 0, "KRW": 0, "KWD": 3, "BHD": 3}.get(currency.upper(), 2)


def stock_breakdown(db: Session, store_id, variant_ids: list) -> dict:
    if not variant_ids:
        return {}
    rows = db.execute(
        select(StockLevel, Warehouse).join(Warehouse, Warehouse.id == StockLevel.warehouse_id)
        .where(StockLevel.variant_id.in_(variant_ids), Warehouse.store_id == store_id)
        .order_by(Warehouse.is_default.desc(), Warehouse.name)).all()
    out: dict = {}
    for lv, wh in rows:
        out.setdefault(lv.variant_id, []).append({
            "warehouse_id": str(wh.id), "warehouse": wh.name, "code": wh.code, "active": wh.is_active,
            "on_hand": lv.on_hand, "reserved": lv.reserved, "available": lv.on_hand - lv.reserved,
            "reorder_point": lv.reorder_point})
    return out


def product_detail(db: Session, p: Principal, product: Product) -> dict:
    variants = db.execute(select(Variant).where(Variant.product_id == product.id)
                          .order_by(Variant.position, Variant.created_at)).scalars().all()
    ids = [v.id for v in variants]
    avail = availability(db, p.store.id, ids)
    per = stock_breakdown(db, p.store.id, ids)
    media = db.execute(select(ProductMedia).where(ProductMedia.product_id == product.id)
                       .order_by(ProductMedia.position, ProductMedia.id)).scalars().all()
    cats = [str(r[0]) for r in db.execute(select(ProductCategory.category_id).where(
        ProductCategory.product_id == product.id).order_by(ProductCategory.position))]
    return {
        "id": str(product.id), "title": product.title, "slug": product.slug, "description": product.description,
        "status": product.status, "product_type": product.product_type,
        "brand_id": str(product.brand_id) if product.brand_id else None, "tags": product.tags or [],
        "options": product.options or [], "attributes": product.attributes or {},
        "seo": {"title": (product.seo or {}).get("title", ""), "description": (product.seo or {}).get("description", "")},
        "tax_class": product.tax_class, "is_demo": product.is_demo, "currency": p.store.currency,
        "published_at": product.published_at.isoformat() if product.published_at else None,
        "created_at": product.created_at.isoformat(), "updated_at": product.updated_at.isoformat(),
        "category_ids": cats,
        "media": [{"id": str(m.id), "url": m.url, "alt": m.alt, "variant_ids": m.variant_ids or []} for m in media],
        "variants": [{
            "id": str(v.id), "sku": v.sku, "barcode": v.barcode, "title": v.title, "option_values": v.option_values,
            "price_minor": v.price_minor, "compare_at_minor": v.compare_at_minor, "cost_minor": v.cost_minor,
            "weight_grams": v.weight_grams, "track_inventory": v.track_inventory,
            "allow_backorder": v.allow_backorder, "is_active": v.is_active,
            "available": avail.get(v.id, 0) if v.track_inventory else None,
            "stock": per.get(v.id, [])} for v in variants],
    }
