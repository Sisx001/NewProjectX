"""Legacy (FastAPI + MongoDB) -> VOLTORA (PostgreSQL) importer.

Properties:
* **Insert-only and idempotent** – every imported record is recorded in `legacy_id_map`; re-running never duplicates
  or overwrites (so a re-run after go-live cannot reset live stock or edited products).
* **Dry-run == apply minus commit** – the same code runs inside one transaction that is rolled back for a dry run,
  so counts, validation failures and reconciliation are exactly what an apply would produce.
* **Per-record savepoints** – one invalid record is reported, it never aborts the run.
* Targets that do not exist in this installation (module not installed) are skipped with an explicit report entry.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import re
import secrets
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from ...core.services import SETTINGS, SYSTEM, Actor, audit, set_settings
from ...db import Base
from ..catalog.models import Brand, Category, Product, ProductCategory, ProductMedia, Variant
from ..customers.models import Address, Customer
from ..identity.models import Membership, Role, User
from ..inventory.models import StockLevel, Warehouse
from ..platform.models import SettingValue, Store
from .models import LegacyIdMap, LegacyImportRun
from .reader import Source

ROLE_MAP = {
    "owner": "owner", "super_admin": "owner", "admin": "store_admin", "store_manager": "store_manager",
    "moderator": "support_agent", "support_agent": "support_agent", "product_manager": "catalog_staff",
    "seller": "catalog_staff", "content_editor": "catalog_staff", "seo_manager": "catalog_staff",
    "designer": "catalog_staff", "order_manager": "fulfillment", "inventory_manager": "fulfillment",
    "finance_manager": "finance", "marketing_manager": "marketer", "analyst": "auditor", "investor": "auditor",
}
STATUS_MAP = {  # legacy order status -> (status, payment_status override, fulfillment_status)
    "pending": ("pending", None, "unfulfilled"),
    "awaiting_payment": ("awaiting_payment", None, "unfulfilled"),
    "processing": ("processing", None, "unfulfilled"),
    "shipped": ("shipped", None, "fulfilled"),
    "out_for_delivery": ("shipped", None, "fulfilled"),
    "delivered": ("delivered", None, "fulfilled"),
    "cancelled": ("cancelled", None, "unfulfilled"),
    "failed": ("cancelled", None, "unfulfilled"),
    "refunded": ("cancelled", "refunded", "unfulfilled"),
    "partially_refunded": ("delivered", "partially_refunded", "fulfilled"),
    "returned": ("returned", None, "fulfilled"),
}
OPEN_STATUSES = ("pending", "awaiting_payment", "processing")
SETTING_TARGETS: dict[str, tuple[str, Any]] = {  # legacy settings.value field -> (canonical key, transform)
    "tagline": ("store.tagline", None), "announcement": ("store.announcement", None),
    "email": ("store.support_email", None), "phone": ("store.support_phone", None), "address": ("store.address", None),
    "mode": ("store.maintenance_mode", lambda v: v == "maintenance"),
    "maintenance_message": ("store.maintenance_message", None), "tax_rate": ("tax.rate_percent", None),
    "tax_inclusive": ("tax.inclusive", None), "return_window_days": ("orders.return_window_days", None),
    "seo_title": ("seo.title", None), "seo_description": ("seo.description", None),
}
SETTING_DESTINATIONS = {
    "shipping": "shipping module: zones & rates (re-create or import from the legacy export by hand)",
    "payments": "payments module: provider/method configuration (credentials are never migrated)",
    "currencies": "store currency settings", "tax_zones": "tax settings", "navigation": "builder: header/footer navigation",
    "footer_links": "builder: footer", "features": "feature flags are replaced by installed modules/plugins",
    "translations": "content: translations", "brand": "store profile (install wizard / Settings → Store)",
    "base_currency": "Store.currency (set during installation)", "timezone": "Store.timezone (set during installation)",
}
KNOWN_FIELDS = {
    "users": {"id", "email", "name", "password_hash", "role", "phone", "phone_verified", "email_verified", "mfa_enabled",
              "mfa_secret", "mfa_pending", "mfa_recovery", "mfa_last_step", "disabled", "ban", "grant", "addresses",
              "bag", "points", "credit_balance", "avatar", "created_at"},
    "products": {"id", "name", "name_bn", "slug", "sku", "brand", "category", "description", "description_bn",
                 "subtitle", "price", "compare_price", "images", "image_alts", "specs", "variants", "warranty",
                 "badge", "featured", "gift_card", "bundle", "preorder", "tiers", "collection", "seo_title",
                 "seo_description", "sale_ends_at", "published", "version", "demo", "created_at", "updated_at"},
    "orders": {"id", "number", "idempotency_key", "request_hash", "transaction_id", "user_id", "customer", "items",
               "subtotal", "bundle_discount", "tier_discount", "discount", "coupon", "points_redeemed",
               "points_discount", "credit_discount", "gift_discount", "gift_code", "shipping", "gift_wrap_fee", "tax",
               "tax_inclusive", "tax_label", "tax_rule", "total", "currency", "payment_method",
               "payment_instructions", "payment_status", "payment_error", "status", "history", "internal_notes",
               "refunds", "refunded_amount", "notes", "marketing", "gift", "gift_message", "affiliate_code",
               "preorder", "ships_at", "tracking", "tracking_source", "stock_released", "reminder_at",
               "bank_reference", "created_at", "updated_at", "gift_restored", "shipping_method", "shipping_estimate"},
    "coupons": {"id", "code", "type", "value", "min_spend", "max_uses", "max_per_customer", "enabled", "starts_at",
                "ends_at", "product_ids", "category_ids", "used", "created_at"},
    "categories": {"id", "name", "slug", "description", "image", "icon", "enabled", "parent_id", "position",
                   "demo_industry", "seo_title"},
}
NOT_MIGRATED = {  # collections present in a legacy export that this importer deliberately does not read
    "sessions": "transient: customers and staff sign in again",
    "login_attempts": "ephemeral", "login_history": "ephemeral", "otps": "ephemeral", "rate_limits": "ephemeral",
    "events": "ephemeral", "previews": "ephemeral", "email_queue": "ephemeral", "notifications": "ephemeral",
    "provider_configs": "credentials are never migrated; re-enter and verify providers in the new payments/shipping UI",
    "integrations": "AI/OAuth keys are never migrated; re-enter in AI/Settings",
    "documents": "pages/themes belong to the new builder document model (manual rebuild; legacy theme JSON kept in the export)",
    "themes": "see documents", "site_workspace": "see documents", "site_releases": "see documents",
    "media": "binary media must be exported to the media directory separately",
    "reviews": "not yet supported by a target module", "conversations": "support threads: no importer yet",
    "messages": "support threads: no importer yet", "gift_cards": "no target module yet",
    "affiliates": "no target module yet", "loyalty_ledger": "no target module yet", "credit_ledger": "no target module yet",
    "return_requests": "no target module yet", "payment_transactions": "provider records; refund/transaction history is preserved in order events only",
    "stock_movements": "history is not replayed; opening balances come from the inventory document",
    "roles": "custom roles have no 1:1 permission mapping; recreate in Team & roles", "audit_logs": "legacy audit trail is retained in the export only",
}


class Invalid(Exception):
    pass


@dataclass
class Stat:
    read: int = 0
    created: int = 0
    linked: int = 0
    skipped_existing: int = 0
    invalid: int = 0
    warnings: int = 0
    note: str | None = None


@dataclass
class Ctx:
    db: Session
    store: Store
    src: Source
    actor: Actor
    legacy_app_secret: str | None = None
    stats: dict[str, Stat] = field(default_factory=lambda: defaultdict(Stat))
    issues: list[dict] = field(default_factory=list)
    unmapped_fields: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    ids: dict[tuple[str, str], uuid.UUID | None] = field(default_factory=dict)
    skipped_targets: dict[str, str] = field(default_factory=dict)
    locations: dict[str, dict] = field(default_factory=dict)
    reserved_orders: set[str] = field(default_factory=set)
    open_reserved: Counter = field(default_factory=Counter)
    warehouse: Warehouse | None = None
    accepted_orders: list[dict] = field(default_factory=list)
    unmapped_settings: dict[str, str] = field(default_factory=dict)

    def issue(self, coll: str, lid: Any, severity: str, reason: str):
        if severity == "warning":
            self.stats[coll].warnings += 1
        if len(self.issues) < 1000:
            self.issues.append({"collection": coll, "legacy_id": str(lid), "severity": severity, "reason": reason})

    def get(self, coll: str, lid: Any) -> uuid.UUID | None:
        return self.ids.get((coll, str(lid))) if lid else None


# ---------------------------------------------------------------- helpers
def _int(v: Any, default: int = 0) -> int:
    if v is None or v == "":
        return default
    try:
        return int(round(float(v)))
    except (TypeError, ValueError) as exc:
        raise Invalid(f"not a number: {v!r}") from exc


def _dt(v: Any) -> dt.datetime | None:
    if not v:
        return None
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.UTC)
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


def normalize_phone(raw: Any) -> tuple[str, bool]:
    """Bangladesh mobile -> 01XXXXXXXXX. Returns (value, normalized_ok)."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith("880"):
        digits = digits[2:]
    if digits and not digits.startswith("0") and len(digits) == 10:
        digits = "0" + digits
    ok = bool(re.fullmatch(r"01[3-9]\d{8}", digits))
    return digits[:16], ok


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "item"


def _model(module: str, cls: str):
    """Model from a module that may not be installed/landed in this build; None when absent."""
    try:
        mod = importlib.import_module(f"app.modules.{module}.models")
        m = getattr(mod, cls)
        return m if m.__table__.name in Base.metadata.tables else None
    except (ImportError, AttributeError):
        return None


def _each(ctx: Ctx, coll: str, rows: list[dict], fn, table: str):
    st = ctx.stats[coll]
    known = KNOWN_FIELDS.get(coll)
    for row in rows:
        st.read += 1
        lid = row.get("id")
        if known:
            for k in row:
                if k not in known:
                    ctx.unmapped_fields[coll][k] += 1
        if not lid:
            st.invalid += 1
            ctx.issue(coll, "?", "invalid", "record has no id")
            continue
        lid = str(lid)
        if (coll, lid) in ctx.ids:
            st.skipped_existing += 1
            continue
        try:
            with ctx.db.begin_nested():
                out = fn(row)
                if out is None:
                    continue
                outcome, target = out
                ctx.db.add(LegacyIdMap(store_id=ctx.store.id, collection=coll, legacy_id=lid, target_table=table,
                                       target_id=target, outcome=outcome))
                ctx.db.flush()
        except Invalid as exc:
            st.invalid += 1
            ctx.issue(coll, lid, "invalid", str(exc))
            continue
        except (IntegrityError, DataError) as exc:
            st.invalid += 1
            ctx.issue(coll, lid, "invalid", str(getattr(exc, "orig", exc)).splitlines()[0][:240])
            continue
        ctx.ids[(coll, lid)] = target
        setattr(st, outcome, getattr(st, outcome) + 1)


def _skip_collection(ctx: Ctx, coll: str, reason: str):
    ctx.skipped_targets[coll] = reason
    ctx.stats[coll].note = f"skipped: {reason}"


def _load_ids(ctx: Ctx):
    for m in ctx.db.execute(select(LegacyIdMap).where(LegacyIdMap.store_id == ctx.store.id)).scalars():
        ctx.ids[(m.collection, m.legacy_id)] = m.target_id


# ---------------------------------------------------------------- taxonomy & catalog
def import_categories(ctx: Ctx):
    rows = ctx.src.list("categories")
    for r in rows:
        r.setdefault("_pos", 0)

    def one(r):
        name = str(r.get("name") or "").strip()
        if not name:
            raise Invalid("category has no name")
        slug = _slug(r.get("slug") or name)
        existing = ctx.db.execute(select(Category).where(Category.store_id == ctx.store.id, Category.slug == slug)).scalar()
        if existing is not None:
            if existing.legacy_id and existing.legacy_id != str(r["id"]):
                raise Invalid(f"slug {slug!r} is already used by another category")
            existing.legacy_id = existing.legacy_id or str(r["id"])
            return "linked", existing.id
        c = Category(store_id=ctx.store.id, name=name[:160], slug=slug[:160], description=r.get("description") or "",
                     image_url=r.get("image") or None, position=_int(r.get("position")),
                     is_active=bool(r.get("enabled", True)), seo={"title": r.get("seo_title") or ""},
                     legacy_id=str(r["id"]))
        ctx.db.add(c)
        ctx.db.flush()
        return "created", c.id

    _each(ctx, "categories", rows, one, "categories")
    for r in rows:  # second pass: hierarchy
        pid, cid = ctx.get("categories", r.get("parent_id")), ctx.get("categories", r.get("id"))
        if r.get("parent_id") and pid is None:
            ctx.issue("categories", r["id"], "warning", f"parent {r['parent_id']} not found; imported at top level")
        if pid and cid and pid != cid:
            c = ctx.db.get(Category, cid)
            if c is not None and c.parent_id is None and c.legacy_id == str(r["id"]):
                c.parent_id = pid


def import_brands(ctx: Ctx):
    def one(r):
        name = str(r.get("name") or "").strip()
        if not name:
            raise Invalid("brand has no name")
        slug = _slug(r.get("slug") or name)
        existing = ctx.db.execute(select(Brand).where(Brand.store_id == ctx.store.id, Brand.slug == slug)).scalar()
        if existing is not None:
            return "linked", existing.id
        b = Brand(store_id=ctx.store.id, name=name[:160], slug=slug[:160], logo_url=r.get("logo") or r.get("image") or None)
        ctx.db.add(b)
        ctx.db.flush()
        return "created", b.id

    _each(ctx, "brands", ctx.src.list("brands"), one, "brands")


def _brand_for(ctx: Ctx, value: Any) -> uuid.UUID | None:
    if not value:
        return None
    bid = ctx.get("brands", value)
    if bid:
        return bid
    name = str(value)
    existing = ctx.db.execute(select(Brand).where(Brand.store_id == ctx.store.id, Brand.slug == _slug(name))).scalar()
    if existing is None:
        existing = Brand(store_id=ctx.store.id, name=name[:160], slug=_slug(name)[:160])
        ctx.db.add(existing)
        ctx.db.flush()
    return existing.id


def import_products(ctx: Ctx):
    def one(r):
        title = str(r.get("name") or "").strip()
        if not title:
            raise Invalid("product has no name")
        base_price = _int(r.get("price"))
        slug = _slug(r.get("slug") or title)[:300]
        existing = ctx.db.execute(select(Product).where(Product.store_id == ctx.store.id, Product.slug == slug)).scalar()
        if existing is not None:
            if existing.legacy_id and existing.legacy_id != str(r["id"]):
                raise Invalid(f"slug {slug!r} is already used by another product")
            existing.legacy_id = existing.legacy_id or str(r["id"])
            return "linked", existing.id
        legacy_variants = r.get("variants") or []
        options: dict[str, list] = {}
        for v in legacy_variants:
            for k, val in (v.get("options") or {}).items():
                options.setdefault(k, [])
                if val not in options[k]:
                    options[k].append(val)
        attrs = {k: r[k] for k in ("name_bn", "subtitle", "warranty", "badge", "featured", "gift_card", "bundle",
                                   "preorder", "tiers", "collection", "sale_ends_at", "image_alts", "specs",
                                   "description_bn") if r.get(k) not in (None, "", {}, [])}
        published = bool(r.get("published"))
        p = Product(store_id=ctx.store.id, title=title[:300], slug=slug, description=r.get("description") or "",
                    status="active" if published else "draft", brand_id=_brand_for(ctx, r.get("brand")),
                    tags=[t for t in [r.get("badge")] if t], options=[{"name": k, "values": v} for k, v in options.items()],
                    attributes=attrs, seo={"title": r.get("seo_title") or "", "description": r.get("seo_description") or ""},
                    is_demo=bool(r.get("demo")), published_at=_dt(r.get("created_at")) if published else None,
                    legacy_id=str(r["id"]))
        ctx.db.add(p)
        ctx.db.flush()
        cat = ctx.get("categories", r.get("category"))
        if r.get("category") and cat is None:
            ctx.issue("products", r["id"], "warning", f"category {r['category']} not imported")
        if cat:
            ctx.db.add(ProductCategory(product_id=p.id, category_id=cat))
        for i, url in enumerate(r.get("images") or []):
            ctx.db.add(ProductMedia(product_id=p.id, url=str(url)[:800], alt=(r.get("image_alts") or {}).get(url, ""), position=i))
        if not legacy_variants:
            legacy_variants = [{"id": f"{r['id']}:default", "sku": r.get("sku") or f"LEG-{str(r['id'])[:12]}", "options": {},
                                "price": base_price, "compare_price": r.get("compare_price")}]
            ctx.issue("products", r["id"], "warning", "no embedded variants; created one default variant")
        for i, v in enumerate(legacy_variants):
            opts = v.get("options") or {}
            sku = str(v.get("sku") or r.get("sku") or "").strip()
            if not sku:
                raise Invalid("variant has no sku")
            clash = ctx.db.execute(select(Variant.id).where(Variant.store_id == ctx.store.id, Variant.sku == sku)).scalar()
            if clash:
                raise Invalid(f"sku {sku!r} already exists")
            price = _int(v.get("price"), base_price) if v.get("price") is not None else base_price
            compare = v.get("compare_price") if v.get("compare_price") is not None else r.get("compare_price")
            var = Variant(store_id=ctx.store.id, product_id=p.id, sku=sku[:80], title=(" / ".join(map(str, opts.values())) or "Default")[:200],
                          option_values=opts, price_minor=price, compare_at_minor=_int(compare) or None, position=i,
                          is_active=True, legacy_id=str(v.get("id") or f"{r['id']}:{i}"))
            ctx.db.add(var)
            ctx.db.flush()
            ctx.ids[("variants", var.legacy_id)] = var.id
            ctx.db.add(LegacyIdMap(store_id=ctx.store.id, collection="variants", legacy_id=var.legacy_id,
                                   target_table="variants", target_id=var.id, outcome="created"))
            ctx.stats["variants"].read += 1
            ctx.stats["variants"].created += 1
        return "created", p.id

    # Savepoint rollback on failure must also forget variant ids registered inside the failed record.
    before = dict(ctx.ids)
    rows = ctx.src.list("products")
    st = ctx.stats["products"]
    for r in rows:
        snapshot = dict(ctx.ids)
        v_before = (ctx.stats["variants"].read, ctx.stats["variants"].created)
        inv_before = st.invalid
        _each(ctx, "products", [r], one, "products")
        if st.invalid > inv_before:
            ctx.ids = snapshot
            ctx.stats["variants"].read, ctx.stats["variants"].created = v_before
    del before
    # variants of products that were already imported/linked still need their ids for orders/stock
    for m in ctx.db.execute(select(Variant.legacy_id, Variant.id).where(Variant.store_id == ctx.store.id,
                                                                         Variant.legacy_id.is_not(None))):
        ctx.ids.setdefault(("variants", m.legacy_id), m.id)


# ---------------------------------------------------------------- users & customers
def _unseal_legacy(secret: str, token: str) -> str | None:
    import base64

    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def _address_name(ctx: Ctx, loc_id: Any) -> str:
    if not loc_id:
        return ""
    row = ctx.locations.get(str(loc_id))
    if row:
        return row.get("name") or ""
    return str(loc_id).split(":", 1)[-1].replace("-", " ").title()


def import_users(ctx: Ctx):
    from ...core.security import seal

    roles: dict[str, Role] = {r.key: r for r in ctx.db.execute(select(Role).where(Role.store_id == ctx.store.id)).scalars()}

    def one(r):
        email = str(r.get("email") or "").strip().lower()
        if "@" not in email:
            raise Invalid("missing or invalid email")
        role = str(r.get("role") or "customer")
        kind = "customer" if role == "customer" else "staff"
        existing = ctx.db.execute(select(User).where(User.email == email)).scalar()
        if existing is not None:
            if kind == "customer" and ctx.db.execute(select(Customer.id).where(Customer.user_id == existing.id)).first() is None:
                ctx.db.add(Customer(store_id=ctx.store.id, user_id=existing.id, email=email, name=existing.name or "",
                                    legacy_id=str(r["id"])))
            ctx.issue("users", r["id"], "warning", "email already exists in the new system; linked, nothing overwritten")
            return "linked", existing.id
        phone, ok = normalize_phone(r.get("phone")) if r.get("phone") else (None, True)
        pw = r.get("password_hash")
        usable = isinstance(pw, str) and pw.startswith("$argon2")
        if not usable:
            ctx.issue("users", r["id"], "warning", "no argon2 password hash; user must reset their password")
        disabled = bool(r.get("disabled")) or bool((r.get("ban") or {}).get("active"))
        u = User(email=email, name=str(r.get("name") or "")[:160], phone=phone or None, kind=kind,
                 status="disabled" if disabled else "active", password_hash=pw if usable else None,
                 email_verified_at=_dt(r.get("created_at")) if r.get("email_verified") else None,
                 marketing_consent=False, legacy_id=str(r["id"]))
        if r.get("mfa_enabled"):
            secret = _unseal_legacy(ctx.legacy_app_secret, r["mfa_secret"]) if ctx.legacy_app_secret and r.get("mfa_secret") else None
            if secret:
                u.mfa_secret_sealed, u.mfa_enabled_at = seal(secret), dt.datetime.now(dt.UTC)
            else:
                ctx.issue("users", r["id"], "warning", "MFA was enabled; secret not migrated (no/invalid legacy app secret): re-enrol")
        ctx.db.add(u)
        ctx.db.flush()
        if kind == "staff":
            key = ROLE_MAP.get(role)
            if key and key in roles:
                ctx.db.add(Membership(user_id=u.id, store_id=ctx.store.id, role_id=roles[key].id))
            else:
                ctx.issue("users", r["id"], "warning", f"legacy role {role!r} has no built-in equivalent; no access granted, assign a role")
        else:
            c = Customer(store_id=ctx.store.id, user_id=u.id, email=email, phone=phone or None, name=u.name,
                         legacy_id=str(r["id"]))
            ctx.db.add(c)
            ctx.db.flush()
            for i, a in enumerate(r.get("addresses") or []):
                line = str(a.get("address_line") or "").strip()
                district = _address_name(ctx, a.get("district_id"))
                if not line or not district:
                    ctx.issue("users", r["id"], "warning", f"address {i} incomplete (needs address line and district); skipped")
                    continue
                aphone, _ = normalize_phone(a.get("phone") or phone or "")
                ctx.db.add(Address(customer_id=c.id, label=str(a.get("label") or "Home")[:40], name=u.name or email, phone=aphone or "",
                                   line1=line[:300], area=_address_name(ctx, a.get("area_id"))[:120],
                                   upazila=_address_name(ctx, a.get("upazila_id"))[:120], district=district[:120],
                                   division=_address_name(ctx, a.get("division_id"))[:120],
                                   postal_code=str(a.get("postcode") or "")[:12], is_default=bool(a.get("default"))))
        return "created", u.id

    _each(ctx, "users", ctx.src.list("users"), one, "users")


def _customer_for_order(ctx: Ctx, o: dict, phone: str, email: str | None) -> uuid.UUID | None:
    uid = ctx.get("users", o.get("user_id"))
    if uid:
        return ctx.db.execute(select(Customer.id).where(Customer.user_id == uid, Customer.store_id == ctx.store.id)).scalar()
    key = f"phone:{phone}" if phone else (f"email:{email}" if email else None)
    if key is None:
        return None
    hit = ctx.ids.get(("guest_customers", key))
    if hit:
        return hit
    q = select(Customer.id).where(Customer.store_id == ctx.store.id)
    existing = ctx.db.execute(q.where(Customer.phone == phone)).scalars().first() if phone else None
    if existing is None and email:
        existing = ctx.db.execute(q.where(Customer.email == email)).scalars().first()
    if existing is None:
        c = Customer(store_id=ctx.store.id, email=email, phone=phone or None, name=str((o.get("customer") or {}).get("name") or "")[:160])
        ctx.db.add(c)
        ctx.db.flush()
        existing = c.id
        ctx.stats["guest_customers"].created += 1
    ctx.stats["guest_customers"].read += 1
    ctx.db.add(LegacyIdMap(store_id=ctx.store.id, collection="guest_customers", legacy_id=key, target_table="customers",
                           target_id=existing, outcome="created"))
    ctx.ids[("guest_customers", key)] = existing
    return existing


# ---------------------------------------------------------------- inventory warehouse
def _warehouse(ctx: Ctx) -> Warehouse:
    if ctx.warehouse is None:
        q = select(Warehouse).where(Warehouse.store_id == ctx.store.id)
        w = (ctx.db.execute(q.where(Warehouse.code == "MAIN")).scalars().first()
             or ctx.db.execute(q.where(Warehouse.is_default.is_(True))).scalars().first())
        if w is None:
            w = Warehouse(store_id=ctx.store.id, name="Main warehouse", code="MAIN", is_default=True)
            ctx.db.add(w)
            ctx.db.flush()
        ctx.warehouse = w
    return ctx.warehouse


# ---------------------------------------------------------------- discounts
def import_coupons(ctx: Ctx):
    Discount = _model("discounts", "Discount")
    if Discount is None:
        return _skip_collection(ctx, "coupons", "discounts module/table not installed")

    def one(r):
        code = str(r.get("code") or "").strip().upper()
        if not code:
            raise Invalid("coupon has no code")
        existing = ctx.db.execute(select(Discount).where(Discount.store_id == ctx.store.id, Discount.code == code)).scalar()
        if existing is not None:
            return "linked", existing.id
        kind = "fixed" if r.get("type") == "fixed" else "percentage"
        value = _int(r.get("value"))
        if kind == "percentage" and not 1 <= value <= 100:
            raise Invalid(f"percentage out of range: {value}")
        pids = [str(ctx.get("products", p)) for p in r.get("product_ids") or [] if ctx.get("products", p)]
        cids = [str(ctx.get("categories", c)) for c in r.get("category_ids") or [] if ctx.get("categories", c)]
        if len(pids) < len(r.get("product_ids") or []) or len(cids) < len(r.get("category_ids") or []):
            ctx.issue("coupons", r["id"], "warning", "some product/category scopes were not imported and were dropped")
        limit = _int(r.get("max_uses"), 0) or None
        used = _int(r.get("used"))
        d = Discount(store_id=ctx.store.id, code=code[:40], description="Imported from legacy coupon", kind=kind,
                     percent=value if kind == "percentage" else None, amount_minor=value if kind == "fixed" else None,
                     min_subtotal_minor=_int(r.get("min_spend")), applies_to="products" if pids else "categories" if cids else "all",
                     product_ids=pids, category_ids=cids, starts_at=_dt(r.get("starts_at")), ends_at=_dt(r.get("ends_at")),
                     usage_limit=limit, per_customer_limit=_int(r.get("max_per_customer")) or None,
                     used_count=min(used, limit) if limit else used, active=bool(r.get("enabled", True)))
        ctx.db.add(d)
        ctx.db.flush()
        return "created", d.id

    _each(ctx, "coupons", ctx.src.list("coupons"), one, "discounts")


# ---------------------------------------------------------------- orders
def import_orders(ctx: Ctx):
    Order, OrderLine, OrderEvent = (_model("commerce", n) for n in ("Order", "OrderLine", "OrderEvent"))
    if Order is None or OrderLine is None:
        return _skip_collection(ctx, "orders", "commerce module/tables not installed")
    Discount, Redemption = _model("discounts", "Discount"), _model("discounts", "DiscountRedemption")
    wh = _warehouse(ctx) if ctx.reserved_orders else None

    def one(o):
        status = str(o.get("status") or "")
        if status not in STATUS_MAP:
            raise Invalid(f"status {status!r} is not importable (hidden/unknown legacy state)")
        st, pay_override, fulfil = STATUS_MAP[status]
        c = o.get("customer") or {}
        phone, ok = normalize_phone(c.get("phone"))
        if not phone:
            raise Invalid("order has no customer phone")
        if not ok:
            ctx.issue("orders", o["id"], "warning", f"phone {c.get('phone')!r} is not a valid Bangladesh mobile; stored digits only")
        email = (str(c.get("email") or "").strip().lower() or None)
        number = str(o.get("number") or "VT-" + str(o["id"])[:8].upper())[:24]
        if ctx.db.execute(select(Order.id).where(Order.number == number)).first():
            raise Invalid(f"order number {number} already exists")
        total, subtotal = _int(o.get("total")), _int(o.get("subtotal"))
        disc = sum(_int(o.get(k)) for k in ("discount", "bundle_discount", "tier_discount", "points_discount",
                                             "credit_discount", "gift_discount"))
        shipping, wrap, tax = _int(o.get("shipping")), _int(o.get("gift_wrap_fee")), _int(o.get("tax"))
        inclusive = bool(o.get("tax_inclusive", True))
        expected = subtotal - disc + shipping + wrap + (0 if inclusive else tax)
        if expected != total:
            ctx.issue("orders", o["id"], "warning", f"component sum {expected} differs from legacy total {total} (legacy total kept)")
        pay = pay_override or str(o.get("payment_status") or "unpaid")
        refunded = _int(o.get("refunded_amount"))
        paid = total if pay in ("paid", "partially_refunded", "refunded") else 0
        if refunded > paid:
            paid = refunded
            ctx.issue("orders", o["id"], "warning", "refunded amount exceeded paid; paid raised to match")
        loc = c.get("location") or {}
        def loc_name(k):
            v = loc.get(k)
            return (v.get("name") if isinstance(v, dict) else None) or ""
        ship = {"name": c.get("name") or "", "phone": phone, "line1": c.get("address") or "",
                "district": loc_name("district_id") or c.get("city") or "", "division": loc_name("division_id"),
                "upazila": loc_name("upazila_id"), "area": loc_name("area_id"), "postal_code": c.get("postal_code") or ""}
        hist = o.get("history") or []
        created = _dt(o.get("created_at")) or dt.datetime.now(dt.UTC)
        order = Order(
            store_id=ctx.store.id, number=number, idempotency_key=str(o.get("idempotency_key") or f"legacy:{o['id']}")[:100],
            request_hash=str(o.get("request_hash") or "")[:64],
            access_token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),  # legacy HMAC links cannot be honoured
            customer_id=_customer_for_order(ctx, o, phone, email), user_id=ctx.get("users", o.get("user_id")),
            email=email, phone=phone, name=str(c.get("name") or "")[:160], status=st, payment_status=pay[:30],
            fulfillment_status=fulfil, payment_method=str(o.get("payment_method") or "cod")[:40],
            currency=str(o.get("currency") or ctx.store.currency)[:3], subtotal_minor=subtotal, discount_minor=disc,
            shipping_minor=shipping, payment_fee_minor=wrap, tax_minor=tax, tax_inclusive=inclusive, total_minor=total,
            paid_minor=paid, refunded_minor=refunded, discount_code=(str(o["coupon"]).upper()[:40] if o.get("coupon") else None),
            shipping_label=str(o.get("shipping_method") or "")[:160], shipping_address=ship, billing_address=ship,
            note=str(o.get("notes") or ""), tags=[t for t, f in (("preorder", o.get("preorder")), ("gift", (o.get("gift") or {}).get("enabled"))) if f],
            cancel_reason=("payment failed (legacy status: failed)" if status == "failed" else None) if st == "cancelled" else None,
            placed_at=created, cancelled_at=_dt(o.get("updated_at")) if st == "cancelled" else None,
            delivered_at=next((_dt(h.get("at")) for h in reversed(hist) if h.get("status") == "delivered"), None) if st == "delivered" else None,
            is_test=bool(o.get("demo")),
        )
        ctx.db.add(order)
        ctx.db.flush()
        reserved_here = o["id"] in ctx.reserved_orders
        for it in o.get("items") or []:
            vid = ctx.get("variants", it.get("variant_id"))
            pid = ctx.get("products", it.get("product_id"))
            if vid is None:
                ctx.issue("orders", o["id"], "warning", f"variant {it.get('variant_id')} not imported; line kept without a link")
            qty = _int(it.get("quantity"), 1)
            opts = it.get("options") or {}
            ctx.db.add(OrderLine(order_id=order.id, variant_id=vid, product_id=pid, sku=str(it.get("sku") or "")[:80],
                                 title=str(it.get("name") or "Item")[:300], variant_title=" / ".join(map(str, opts.values()))[:200],
                                 image_url=it.get("image") or None, quantity=qty, unit_price_minor=_int(it.get("price")),
                                 discount_minor=_int(it.get("tier_saving")), total_minor=_int(it.get("total")),
                                 warehouse_id=wh.id if reserved_here and wh else None,
                                 reserved_qty=qty if reserved_here and vid else 0,
                                 fulfilled_qty=qty if fulfil == "fulfilled" else 0))
            if reserved_here and vid:
                ctx.open_reserved[vid] += qty
        ctx.db.add(OrderEvent(order_id=order.id, kind="placed", message="Imported from legacy VOLTORA", public=False,
                              created_at=created, data={"legacy": {k: o.get(k) for k in (
                                  "id", "subtotal", "bundle_discount", "tier_discount", "discount", "points_discount",
                                  "credit_discount", "gift_discount", "gift_wrap_fee", "tax", "tax_label", "total",
                                  "payment_instructions", "bank_reference", "tracking", "affiliate_code") if o.get(k) not in (None, "")}}))
        for h in hist:
            ctx.db.add(OrderEvent(order_id=order.id, kind="status", message=f"{h.get('status')}: {h.get('note') or ''}".strip(": "),
                                  public=False, actor_label=str(h.get("actor") or "")[:200] or None, created_at=_dt(h.get("at")) or created))
        for n in o.get("internal_notes") or []:
            ctx.db.add(OrderEvent(order_id=order.id, kind="note", message=str(n.get("text") or ""), public=False,
                                  actor_label=str(n.get("actor") or "")[:200] or None, created_at=_dt(n.get("at")) or created))
        for rf in o.get("refunds") or []:
            ctx.db.add(OrderEvent(order_id=order.id, kind="refund", message=f"Refund of {_int(rf.get('amount'))} minor units ({rf.get('method') or 'n/a'}): {rf.get('note') or ''}",
                                  public=False, created_at=_dt(rf.get("at")) or created, data={"legacy_refund": {"amount": _int(rf.get("amount")), "lines": rf.get("lines") or []}}))
        if o.get("coupon") and Discount is not None and Redemption is not None and st != "cancelled":
            d = ctx.db.execute(select(Discount).where(Discount.store_id == ctx.store.id, Discount.code == str(o["coupon"]).upper())).scalar()
            if d is not None:
                key = f"user:{order.user_id}" if order.user_id else f"phone:{phone}"
                ctx.db.add(Redemption(discount_id=d.id, order_id=order.id, customer_key=key, amount_minor=_int(o.get("discount")),
                                      created_at=created))
        ctx.accepted_orders.append(o)
        return "created", order.id

    # users/customers counters are recomputed after the loop
    rows = [o for o in ctx.src.list("orders")]
    rows.sort(key=lambda o: str(o.get("created_at") or ""))
    _each(ctx, "orders", rows, one, "orders")
    ctx.db.flush()
    cust_ids = {c for c in ctx.db.execute(select(Order.customer_id).join(LegacyIdMap, LegacyIdMap.target_id == Order.id)
                                           .where(LegacyIdMap.collection == "orders", LegacyIdMap.store_id == ctx.store.id)).scalars() if c}
    for cid in cust_ids:
        n, spent, last = ctx.db.execute(select(func.count(), func.coalesce(func.sum(Order.total_minor), 0), func.max(Order.placed_at))
                                        .where(Order.customer_id == cid, Order.status != "cancelled")).one()
        cust = ctx.db.get(Customer, cid)
        if cust is not None:
            cust.orders_count, cust.total_spent_minor, cust.last_order_at = n, int(spent), last


def import_inventory(ctx: Ctx):
    from ..inventory.ledger import apply_movement

    doc = next((d for d in ctx.src.list("inventory") if d.get("id") == "main"), None)
    if doc is None:
        ctx.stats["inventory"].note = "no inventory document 'main' in the export"
        return
    qty = doc.get("quantities") or {}
    st = ctx.stats["inventory"]
    wh = _warehouse(ctx)
    for vlid, q in qty.items():
        st.read += 1
        if ("inventory", vlid) in ctx.ids:
            st.skipped_existing += 1
            continue
        vid = ctx.get("variants", vlid)
        if vid is None:
            st.invalid += 1
            ctx.issue("inventory", vlid, "invalid", "variant was not imported")
            continue
        try:
            q = int(q)
            if q < 0:
                ctx.issue("inventory", vlid, "warning", f"negative legacy quantity {q} clamped to 0")
                q = 0
            reserved = ctx.open_reserved.get(vid, 0)
            with ctx.db.begin_nested():
                # legacy quantities are net of open reservations; the ledger tracks on_hand including reserved stock
                apply_movement(ctx.db, store_id=ctx.store.id, variant_id=vid, warehouse_id=wh.id, reason="count",
                               delta_on_hand=q + reserved, ref_type="legacy_import", ref_id=vlid,
                               note="Opening balance from legacy inventory document")
                if reserved:
                    apply_movement(ctx.db, store_id=ctx.store.id, variant_id=vid, warehouse_id=wh.id, reason="reserve",
                                   delta_reserved=reserved, ref_type="legacy_import", ref_id=vlid,
                                   note="Open legacy orders hold this stock")
                ctx.db.add(LegacyIdMap(store_id=ctx.store.id, collection="inventory", legacy_id=vlid, target_table="stock_levels",
                                       target_id=vid, outcome="created"))
                ctx.db.flush()
        except Exception as exc:  # noqa: BLE001
            st.invalid += 1
            ctx.issue("inventory", vlid, "invalid", str(exc)[:200])
            continue
        ctx.ids[("inventory", vlid)] = vid
        st.created += 1
    unmapped_levels = [v for v in ctx.db.execute(select(Variant.id).where(Variant.store_id == ctx.store.id, Variant.legacy_id.is_not(None))).scalars()
                       if ctx.db.get(StockLevel, (v, wh.id)) is None]
    if unmapped_levels:
        ctx.issue("inventory", "-", "warning", f"{len(unmapped_levels)} imported variants have no legacy stock entry (stock 0)")


def import_settings(ctx: Ctx):
    doc = next((d for d in ctx.src.list("settings") if d.get("id") == "store"), None)
    st = ctx.stats["settings"]
    if doc is None:
        st.note = "no settings document 'store' in the export"
        return
    value = doc.get("value") or {}
    changes = {}
    unmapped: dict[str, str] = {}
    for k, v in value.items():
        st.read += 1
        target = SETTING_TARGETS.get(k)
        if target is None or target[0] not in SETTINGS:
            unmapped[k] = (f"no canonical setting {target[0]!r} in this installation" if target
                           else SETTING_DESTINATIONS.get(k, "no canonical setting"))
            continue
        key, fn = target
        if ctx.db.get(SettingValue, (ctx.store.id, key)) is not None:
            st.skipped_existing += 1
            continue
        changes[key] = fn(v) if fn else v
    applied = 0
    for key, v in changes.items():  # one by one so a single invalid legacy value does not discard the rest
        try:
            with ctx.db.begin_nested():
                applied += len(set_settings(ctx.db, ctx.store.id, {key: v}, ctx.actor, note="Imported from legacy settings"))
        except Exception as exc:  # noqa: BLE001
            st.invalid += 1
            ctx.issue("settings", key, "invalid", str(exc)[:200])
    st.created = applied
    st.note = f"{len(unmapped)} legacy fields have no canonical setting (see unmapped_settings)"
    ctx.unmapped_fields["settings"] = Counter(unmapped.keys())
    ctx.stats["settings"].note = st.note
    ctx.unmapped_settings = unmapped


# ---------------------------------------------------------------- reconciliation
def reconcile(ctx: Ctx) -> dict:
    """Source totals vs target totals for legacy-mapped rows. Stock comparison is only meaningful right after import."""
    out: dict[str, Any] = {}
    Order = _model("commerce", "Order")
    if Order is not None and "orders" not in ctx.skipped_targets:
        ids = [v for (c, _), v in ctx.ids.items() if c == "orders" and v]
        src = [o for o in ctx.src.list("orders") if o.get("status") in STATUS_MAP and ("orders", str(o["id"])) in ctx.ids]
        t_total, t_ref, t_n = ctx.db.execute(select(func.coalesce(func.sum(Order.total_minor), 0), func.coalesce(func.sum(Order.refunded_minor), 0),
                                                    func.count()).where(Order.id.in_(ids))).one() if ids else (0, 0, 0)
        s_total = sum(_int(o.get("total")) for o in src)
        s_ref = sum(_int(o.get("refunded_amount")) for o in src)
        out["orders"] = {"source_count": len(src), "target_count": t_n, "source_total_minor": s_total, "target_total_minor": int(t_total),
                         "source_refunded_minor": s_ref, "target_refunded_minor": int(t_ref),
                         "ok": len(src) == t_n and s_total == int(t_total) and s_ref == int(t_ref)}
    doc = next((d for d in ctx.src.list("inventory") if d.get("id") == "main"), None)
    if doc is not None:
        mapped = {k: ctx.ids[("variants", k)] for k in (doc.get("quantities") or {}) if ("inventory", k) in ctx.ids}
        s_qty = sum(max(int(doc["quantities"][k]), 0) for k in mapped)
        vids = list(mapped.values())
        t_qty = ctx.db.execute(select(func.coalesce(func.sum(StockLevel.on_hand - StockLevel.reserved), 0)).where(StockLevel.variant_id.in_(vids))).scalar() if vids else 0
        out["stock"] = {"variants": len(vids), "source_available_units": s_qty, "target_available_units": int(t_qty), "ok": s_qty == int(t_qty),
                        "note": "valid immediately after import; live sales change target stock afterwards"}
    for coll in ("products", "variants", "categories"):
        s_n = len(ctx.src.list(coll)) if coll != "variants" else sum(max(len(p.get("variants") or []), 1) for p in ctx.src.list("products"))
        t_n = sum(1 for (c, _) in ctx.ids if c == coll)
        out[coll] = {"source_count": s_n, "mapped_count": t_n, "ok": s_n == t_n}
    return out


# ---------------------------------------------------------------- orchestration
def run_import(db: Session, store: Store, source_dir: str, *, apply: bool, prefix: str = "", actor: Actor = SYSTEM,
               legacy_app_secret: str | None = None) -> dict:
    src = Source(source_dir, prefix)
    ctx = Ctx(db=db, store=store, src=src, actor=actor, legacy_app_secret=legacy_app_secret)
    _load_ids(ctx)
    ctx.locations = {str(r["id"]): r for r in src.list("locations") if r.get("id")}
    inv = next((d for d in src.list("inventory") if d.get("id") == "main"), None)
    ctx.reserved_orders = set(((inv or {}).get("reservations") or {}).keys())
    present = src.available()
    try:
        import_categories(ctx)
        import_brands(ctx)
        import_products(ctx)
        import_users(ctx)
        import_coupons(ctx)
        import_orders(ctx)
        import_inventory(ctx)
        import_settings(ctx)
        recon = reconcile(ctx)
        report = {
            "mode": "apply" if apply else "dry_run", "source": str(src.dir), "prefix": prefix, "store": store.slug,
            "collections": {k: vars(v) for k, v in ctx.stats.items()},
            "totals": {"invalid": sum(s.invalid for s in ctx.stats.values()), "warnings": sum(s.warnings for s in ctx.stats.values()),
                       "created": sum(s.created for s in ctx.stats.values())},
            "skipped_targets": ctx.skipped_targets,
            "unmapped_fields": {k: dict(v) for k, v in ctx.unmapped_fields.items() if v and k != "settings"},
            "unmapped_settings": ctx.unmapped_settings,
            "not_migrated": {c: NOT_MIGRATED[c] for c in present if c in NOT_MIGRATED},
            "unknown_collections": [c for c in present if c not in NOT_MIGRATED and c not in KNOWN_FIELDS
                                    and c not in ("brands", "inventory", "settings", "locations")],
            "issues": ctx.issues, "reconciliation": recon,
        }
        report["reconciliation_ok"] = all(v.get("ok", True) for v in recon.values())
        if apply:
            audit(db, store.id, actor, "legacy_import.applied", "legacy_import", None,
                  {"created": report["totals"]["created"], "invalid": report["totals"]["invalid"], "source": str(src.dir)})
        if apply:
            db.add(LegacyImportRun(store_id=store.id, mode="apply", source=str(src.dir)[:400],
                                   summary={"collections": report["collections"], "totals": report["totals"],
                                            "reconciliation_ok": report["reconciliation_ok"]}))
            db.commit()
        else:
            db.rollback()
        return report
    except Exception:
        db.rollback()
        raise
