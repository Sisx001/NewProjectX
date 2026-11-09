"""Catalog domain helpers: sanitizer, slugs, stock availability, serialisation, CSV, admin-shell hooks."""
from __future__ import annotations

import html
import re
import unicodedata
import uuid
from html.parser import HTMLParser
from urllib.parse import urlparse

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ...core.deps import Principal
from ...core.services import SEARCHERS, event_type
from ..inventory.models import StockLevel, Warehouse
from .models import Category, Product, ProductMedia, Variant

EV_PRODUCT_CREATED = event_type("product.created", "A product was created")
EV_PRODUCT_UPDATED = event_type("product.updated", "A product was changed (status, price, content or variants)")

# ------------------------------------------------------------------ HTML sanitizer
_ALLOWED = {"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "h2", "h3", "h4", "blockquote", "a", "hr",
            "code", "pre", "s"}
_VOID = {"br", "hr"}
_DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math", "textarea"}
_SAFE_SCHEMES = {"http", "https", "mailto", "tel"}


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in _ALLOWED:
            return
        if tag == "a":
            href = ""
            for k, v in attrs:
                if k == "href" and v:
                    href = _safe_href(v)
            self.out.append(f'<a href="{html.escape(href, quote=True)}" rel="noopener noreferrer nofollow">'
                            if href else "<a>")
        else:
            self.out.append(f"<{tag}>")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID and not self.skip:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in _ALLOWED or tag in _VOID or tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def _safe_href(v: str) -> str:
    v = re.sub(r"[\x00-\x20]+", "", v)
    if v.startswith(("/", "#")) and not v.startswith("//"):
        return v
    return v if urlparse(v).scheme.lower() in _SAFE_SCHEMES else ""


def sanitize_html(raw: str | None) -> str:
    s = _Sanitizer()
    s.feed(raw or "")
    s.close()
    return s.result()


def strip_tags(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()


# ------------------------------------------------------------------ slugs
def slugify(text: str, fallback: str = "item") -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:120]
    return t or f"{fallback}-{uuid.uuid4().hex[:6]}"


def unique_slug(db: Session, model, store_id, text: str, exclude_id=None, fallback: str = "item") -> str:
    base = slugify(text, fallback)
    slug, n = base, 1
    while True:
        q = select(model.id).where(model.store_id == store_id, model.slug == slug)
        if exclude_id:
            q = q.where(model.id != exclude_id)
        if db.execute(q.limit(1)).first() is None:
            return slug
        n += 1
        slug = f"{base}-{n}"


# ------------------------------------------------------------------ stock availability
def stock_subquery(store_id):
    """Per-variant availability across ACTIVE warehouses: avail = sum(on_hand - reserved), reorder = sum(reorder_point)."""
    return (
        select(StockLevel.variant_id.label("vid"),
               func.coalesce(func.sum(StockLevel.on_hand - StockLevel.reserved), 0).label("avail"),
               func.coalesce(func.sum(StockLevel.reorder_point), 0).label("reorder"))
        .join(Warehouse, Warehouse.id == StockLevel.warehouse_id)
        .where(Warehouse.store_id == store_id, Warehouse.is_active.is_(True))
        .group_by(StockLevel.variant_id).subquery("stk")
    )


def availability(db: Session, store_id, variant_ids: list) -> dict[uuid.UUID, int]:
    if not variant_ids:
        return {}
    stk = stock_subquery(store_id)
    rows = db.execute(select(stk.c.vid, stk.c.avail).where(stk.c.vid.in_(variant_ids))).all()
    return {r[0]: int(r[1]) for r in rows}


def variant_sellable(v: Variant, avail: int | None) -> bool:
    if not v.is_active:
        return False
    return (not v.track_inventory) or v.allow_backorder or (avail or 0) > 0


def product_aggregates(store_id, only_active_variants: bool = True):
    """Subquery with one row per product: price range, variant count, sellable flag, tracked stock, low flag."""
    stk = stock_subquery(store_id)
    avail = func.coalesce(stk.c.avail, 0)
    sellable = or_(Variant.track_inventory.is_(False), Variant.allow_backorder.is_(True), avail > 0)
    low = (Variant.track_inventory.is_(True)) & (stk.c.reorder > 0) & (avail <= stk.c.reorder)
    q = (select(Variant.product_id.label("pid"),
                func.min(Variant.price_minor).label("pmin"), func.max(Variant.price_minor).label("pmax"),
                func.count(Variant.id).label("vc"),
                func.bool_or(sellable).label("instock"),
                func.bool_or(low).label("low"),
                func.coalesce(func.sum(case((Variant.track_inventory.is_(True), avail), else_=0)), 0).label("tracked"))
         .outerjoin(stk, stk.c.vid == Variant.id).where(Variant.store_id == store_id))
    if only_active_variants:
        q = q.where(Variant.is_active.is_(True))
    return q.group_by(Variant.product_id).subquery("vagg")


# ------------------------------------------------------------------ misc helpers
def like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def descendants(categories: list[Category], root_ids: set) -> set:
    kids: dict = {}
    for c in categories:
        kids.setdefault(c.parent_id, []).append(c.id)
    out, todo = set(), list(root_ids)
    while todo:
        cur = todo.pop()
        if cur in out:
            continue
        out.add(cur)
        todo.extend(kids.get(cur, []))
    return out


def first_images(db: Session, product_ids: list) -> dict[uuid.UUID, list[ProductMedia]]:
    out: dict = {}
    if not product_ids:
        return out
    for m in db.execute(select(ProductMedia).where(ProductMedia.product_id.in_(product_ids))
                        .order_by(ProductMedia.position, ProductMedia.id)).scalars():
        out.setdefault(m.product_id, []).append(m)
    return out


# ------------------------------------------------------------------ admin shell hooks
def _search_products(db: Session, p: Principal, q: str) -> list[dict]:
    if not p.can("catalog.read"):
        return []
    pat = like(q)
    sku_hits = select(Variant.product_id).where(Variant.store_id == p.store.id, Variant.sku.ilike(pat))
    rows = db.execute(select(Product).where(Product.store_id == p.store.id,
                                            or_(Product.title.ilike(pat), Product.id.in_(sku_hits)))
                      .order_by(Product.updated_at.desc()).limit(8)).scalars().all()
    return [{"label": r.title, "sublabel": f"Product · {r.status}", "path": f"/admin/catalog/products/{r.id}",
             "kind": "product"} for r in rows]


SEARCHERS.append(_search_products)


from . import demo  # noqa: E402,F401  (registers the demo seed hook; must stay last)
