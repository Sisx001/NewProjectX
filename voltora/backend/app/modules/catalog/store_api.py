"""Public storefront catalog endpoints (contract: docs/API_CONTRACTS.md "Catalog")."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ...core.deps import Principal, principal, store_dep
from ...core.errors import AppError, NotFound
from ...db import get_db
from .models import Brand, Category, Product, ProductCategory, ProductMedia, Variant
from .service import (
    availability,
    descendants,
    first_images,
    like,
    product_aggregates,
    strip_tags,
    variant_sellable,
)

store_router = APIRouter(prefix="/api/store", tags=["storefront"])
MAX_PAGE_SIZE = 60


def _card_images(imgs: list[ProductMedia]):
    return {"url": imgs[0].url, "alt": imgs[0].alt} if imgs else None


@store_router.get("/categories")
def store_categories(db: Session = Depends(get_db), store=Depends(store_dep)):
    cats = db.execute(select(Category).where(Category.store_id == store.id, Category.is_active.is_(True))
                      .order_by(Category.position, Category.name)).scalars().all()
    pairs = db.execute(select(ProductCategory.category_id, ProductCategory.product_id).join(
        Product, Product.id == ProductCategory.product_id).where(Product.store_id == store.id,
                                                                  Product.status == "active")).all()
    direct: dict = defaultdict(set)
    for cid, pid in pairs:
        direct[cid].add(pid)
    items = []
    for c in cats:
        ids = descendants(cats, {c.id})
        prods: set = set()
        for i in ids:
            prods |= direct.get(i, set())
        items.append({"id": str(c.id), "name": c.name, "slug": c.slug, "parent_id": str(c.parent_id) if c.parent_id else None,
                      "image_url": c.image_url, "product_count": len(prods)})
    return {"items": items}


def _filtered(db: Session, store, request: Request, q, category, brand, pmin, pmax, in_stock):
    agg = product_aggregates(store.id)
    base = select(Product.id).join(agg, agg.c.pid == Product.id).where(Product.store_id == store.id,
                                                                       Product.status == "active")
    q = (q or "").strip()
    if q:
        pat = like(q)
        sku_hit = select(Variant.product_id).where(Variant.store_id == store.id, Variant.is_active.is_(True),
                                                   Variant.sku.ilike(pat))
        brand_hit = select(Brand.id).where(Brand.store_id == store.id, Brand.name.ilike(pat))
        base = base.where(or_(Product.title.ilike(pat), cast(Product.tags, String).ilike(pat),
                              Product.brand_id.in_(brand_hit), Product.id.in_(sku_hit)))
    if category:
        cats = db.execute(select(Category).where(Category.store_id == store.id, Category.is_active.is_(True))).scalars().all()
        root = next((c for c in cats if c.slug == category), None)
        ids = descendants(cats, {root.id}) if root else set()
        base = base.where(Product.id.in_(select(ProductCategory.product_id).where(ProductCategory.category_id.in_(ids))))
    if brand:
        base = base.where(Product.brand_id.in_(select(Brand.id).where(Brand.store_id == store.id, Brand.slug == brand)))
    if pmin is not None:
        base = base.where(agg.c.pmax >= pmin)
    if pmax is not None:
        base = base.where(agg.c.pmin <= pmax)
    if in_stock:
        base = base.where(agg.c.instock.is_(True))
    for key, val in request.query_params.multi_items():
        if key.startswith("option.") and val:
            name = key[len("option."):]
            base = base.where(Product.id.in_(select(Variant.product_id).where(
                Variant.store_id == store.id, Variant.is_active.is_(True), Variant.option_values[name].astext == val)))
    return base, agg


@store_router.get("/products")
def store_products(
    request: Request, q: str = Query("", max_length=100), category: str | None = Query(None, max_length=160),
    brand: str | None = Query(None, max_length=160), min: int | None = Query(None, ge=0),
    max: int | None = Query(None, ge=0), in_stock: int = Query(0, ge=0, le=1),
    sort: str = Query("newest", pattern="^(newest|price_asc|price_desc|title)$"), page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=MAX_PAGE_SIZE), db: Session = Depends(get_db), store=Depends(store_dep),
):
    base, agg = _filtered(db, store, request, q, category, brand, min, max, bool(in_stock))
    ids_q = base.subquery()
    total = db.execute(select(func.count()).select_from(ids_q)).scalar_one()
    order = {"newest": [func.coalesce(Product.published_at, Product.created_at).desc()],
             "price_asc": [agg.c.pmin.asc(), Product.title], "price_desc": [agg.c.pmin.desc(), Product.title],
             "title": [func.lower(Product.title)]}[sort]
    rows = db.execute(base.add_columns(agg.c.pmin, agg.c.pmax, agg.c.vc, agg.c.instock)
                      .order_by(*order, Product.id).limit(page_size).offset((page - 1) * page_size)).all()
    pids = [r[0] for r in rows]
    prods = {p.id: p for p in db.execute(select(Product).where(Product.id.in_(pids))).scalars()}
    media = first_images(db, pids)
    brands = {b.id: b for b in db.execute(select(Brand).where(Brand.store_id == store.id)).scalars()}
    cheapest: dict = {}
    for v in db.execute(select(Variant).where(Variant.product_id.in_(pids), Variant.is_active.is_(True))
                        .order_by(Variant.price_minor)).scalars():
        cheapest.setdefault(v.product_id, v)
    items = []
    for pid, pmin_, pmax_, vc, instock in rows:
        pr = prods[pid]
        cv = cheapest.get(pid)
        cmp_ = cv.compare_at_minor if cv and cv.compare_at_minor and cv.compare_at_minor > cv.price_minor else None
        b = brands.get(pr.brand_id)
        items.append({"id": str(pid), "slug": pr.slug, "title": pr.title, "image": _card_images(media.get(pid, [])),
                      "images_count": len(media.get(pid, [])), "price_minor": int(pmin_), "price_max_minor": int(pmax_),
                      "compare_at_minor": cmp_, "currency": store.currency, "in_stock": bool(instock),
                      "variant_count": vc, "brand": {"name": b.name, "slug": b.slug} if b else None,
                      "is_demo": pr.is_demo})
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "facets": _facets(db, store, base, agg, ids_q)}


def _facets(db: Session, store, base, agg, ids_q) -> dict:
    ids = select(ids_q.c.id)
    brands = db.execute(select(Brand.slug, Brand.name, func.count(Product.id)).join(Product, Product.brand_id == Brand.id)
                        .where(Product.id.in_(ids)).group_by(Brand.slug, Brand.name).order_by(Brand.name)).all()
    cats = db.execute(select(Category.slug, Category.name, func.count(ProductCategory.product_id))
                      .join(ProductCategory, ProductCategory.category_id == Category.id)
                      .where(ProductCategory.product_id.in_(ids), Category.is_active.is_(True))
                      .group_by(Category.slug, Category.name).order_by(Category.name)).all()
    lo, hi = db.execute(select(func.min(agg.c.pmin), func.max(agg.c.pmax)).where(agg.c.pid.in_(ids))).one()
    seen: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for pid, ov in db.execute(select(Variant.product_id, Variant.option_values).where(
            Variant.product_id.in_(ids), Variant.is_active.is_(True))):
        for k, v in (ov or {}).items():
            seen[k][v].add(pid)
    return {"brands": [{"slug": s, "name": n, "count": c} for s, n, c in brands],
            "categories": [{"slug": s, "name": n, "count": c} for s, n, c in cats],
            "price": {"min": int(lo) if lo is not None else None, "max": int(hi) if hi is not None else None},
            "options": {k: [{"value": v, "count": len(pids)} for v, pids in sorted(vals.items())]
                        for k, vals in sorted(seen.items())}}


@store_router.get("/products/{slug}")
def store_product(slug: str, preview: int = Query(0), db: Session = Depends(get_db), store=Depends(store_dep),
                  p: Principal = Depends(principal)):
    pr = db.execute(select(Product).where(Product.store_id == store.id, Product.slug == slug)).scalar_one_or_none()
    staff_preview = bool(preview and p.is_staff and p.can("catalog.read"))
    if pr is None or (pr.status != "active" and not staff_preview):
        raise NotFound("Product")
    variants = db.execute(select(Variant).where(Variant.product_id == pr.id, Variant.is_active.is_(True))
                          .order_by(Variant.position, Variant.created_at)).scalars().all()
    avail = availability(db, store.id, [v.id for v in variants])
    media = db.execute(select(ProductMedia).where(ProductMedia.product_id == pr.id)
                       .order_by(ProductMedia.position, ProductMedia.id)).scalars().all()
    brand = db.get(Brand, pr.brand_id) if pr.brand_id else None
    cats = db.execute(select(Category.name, Category.slug).join(ProductCategory, ProductCategory.category_id == Category.id)
                      .where(ProductCategory.product_id == pr.id, Category.is_active.is_(True))
                      .order_by(ProductCategory.position)).all()
    seo = pr.seo or {}
    return {
        "id": str(pr.id), "slug": pr.slug, "title": pr.title, "description": pr.description,
        "images": [{"url": m.url, "alt": m.alt or pr.title, "variant_ids": m.variant_ids or []} for m in media],
        "options": [{"name": o["name"], "values": o["values"]} for o in pr.options or []],
        "variants": [{"id": str(v.id), "sku": v.sku, "title": v.title, "option_values": v.option_values,
                      "price_minor": v.price_minor,
                      "compare_at_minor": v.compare_at_minor if v.compare_at_minor and v.compare_at_minor > v.price_minor else None,
                      "in_stock": variant_sellable(v, avail.get(v.id, 0)),
                      "available": max(avail.get(v.id, 0), 0) if v.track_inventory else None,
                      "allow_backorder": v.allow_backorder} for v in variants],
        "brand": {"name": brand.name, "slug": brand.slug} if brand else None,
        "categories": [{"name": n, "slug": s} for n, s in cats],
        "seo": {"title": seo.get("title") or pr.title,
                "description": seo.get("description") or strip_tags(pr.description)[:160]},
        "attributes": pr.attributes or {}, "is_demo": pr.is_demo, "currency": store.currency,
        "status": pr.status if staff_preview else "active",
    }


@store_router.get("/search/suggest")
def suggest(q: str = Query("", max_length=100), db: Session = Depends(get_db), store=Depends(store_dep)):
    q = q.strip()
    if len(q) < 2:
        if len(q) == 0:
            raise AppError("query_required", "Type at least 2 characters")
        return {"products": [], "categories": []}
    pat = like(q)
    agg = product_aggregates(store.id)
    rows = db.execute(select(Product, agg.c.pmin).join(agg, agg.c.pid == Product.id)
                      .where(Product.store_id == store.id, Product.status == "active", Product.title.ilike(pat))
                      .order_by((Product.title.ilike(like(q)[:-1].lstrip("%") + "%")).desc(), Product.title).limit(6)).all()
    media = first_images(db, [r[0].id for r in rows])
    cats = db.execute(select(Category).where(Category.store_id == store.id, Category.is_active.is_(True),
                                             Category.name.ilike(pat)).order_by(Category.name).limit(4)).scalars().all()
    return {"products": [{"slug": pr.slug, "title": pr.title, "image": _card_images(media.get(pr.id, [])),
                          "price_minor": int(pm)} for pr, pm in rows],
            "categories": [{"slug": c.slug, "name": c.name} for c in cats]}
