"""Inventory API: warehouses, stock, adjustments, transfers, counts, ledger, low stock, suppliers, purchase orders."""
from __future__ import annotations

import datetime as dt
import re
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, require
from ...core.errors import AppError, Conflict, NotFound
from ...core.services import Page, audit
from ...db import get_db, utcnow
from ..catalog.models import Product, Variant
from ..catalog.service import like
from ..identity.models import User
from .ledger import lock_levels
from .models import PurchaseOrder, PurchaseOrderLine, StockLevel, StockMovement, Supplier, Warehouse
from .service import (
    ADJUST_REASONS,
    adjust_stock,
    count_stock,
    get_variant,
    get_warehouse,
    low_stock_rows_query,
    receive_stock,
    transfer_stock,
)

router = APIRouter(prefix="/api/inventory", tags=["inventory"])
MODULE = {"title": "Inventory", "description": "Warehouses, stock levels, movements, suppliers and purchase orders"}
CODE_RE = re.compile(r"^[A-Z0-9_-]{2,20}$")


def level_out(lv: StockLevel) -> dict:
    return {"variant_id": str(lv.variant_id), "warehouse_id": str(lv.warehouse_id), "on_hand": lv.on_hand,
            "reserved": lv.reserved, "available": lv.on_hand - lv.reserved, "reorder_point": lv.reorder_point}


# ------------------------------------------------------------------ warehouses
class WarehouseIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    code: str | None = Field(default=None, max_length=20)
    address: dict | None = None
    district: str | None = Field(default=None, max_length=80)
    is_default: bool | None = None
    is_active: bool | None = None


def warehouse_out(w: Warehouse, units: int = 0, variants: int = 0) -> dict:
    return {"id": str(w.id), "name": w.name, "code": w.code, "address": w.address or {}, "district": w.district,
            "is_default": w.is_default, "is_active": w.is_active, "units_on_hand": units, "variants_stocked": variants}


@router.get("/warehouses")
def list_warehouses(db: Session = Depends(get_db), p: Principal = Depends(require("inventory.read"))):
    stats = {r[0]: (int(r[1]), r[2]) for r in db.execute(
        select(StockLevel.warehouse_id, func.coalesce(func.sum(StockLevel.on_hand), 0),
               func.count().filter(StockLevel.on_hand > 0)).group_by(StockLevel.warehouse_id))}
    rows = db.execute(select(Warehouse).where(Warehouse.store_id == p.store.id)
                      .order_by(Warehouse.is_default.desc(), Warehouse.name)).scalars().all()
    return {"items": [warehouse_out(w, *stats.get(w.id, (0, 0))) for w in rows]}


def _clean_address(a: dict | None) -> dict:
    keys = ("line1", "area", "upazila", "district", "division")
    return {k: str(a.get(k, ""))[:200] for k in keys if a and a.get(k)}


@router.post("/warehouses", status_code=201)
def create_warehouse(body: WarehouseIn, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    code = (body.code or "").strip().upper()
    if not body.name or not CODE_RE.match(code):
        raise AppError("invalid_warehouse", "A name and a 2–20 character code (A–Z, 0–9, - _) are required")
    if db.execute(select(Warehouse.id).where(Warehouse.store_id == p.store.id, Warehouse.code == code)).first():
        raise Conflict("code_taken", f"Warehouse code {code} is already used")
    has_default = db.execute(select(Warehouse.id).where(Warehouse.store_id == p.store.id,
                                                        Warehouse.is_default.is_(True))).first()
    w = Warehouse(store_id=p.store.id, name=body.name.strip(), code=code, address=_clean_address(body.address),
                  district=body.district, is_default=not has_default, is_active=True)
    db.add(w)
    db.flush()
    if body.is_default and has_default:
        _make_default(db, p, w)
    audit(db, p.store.id, p.actor, "warehouse.created", "warehouse", w.id, {"code": code, "name": w.name})
    db.commit()
    return warehouse_out(w)


def _make_default(db: Session, p: Principal, w: Warehouse) -> None:
    for o in db.execute(select(Warehouse).where(Warehouse.store_id == p.store.id, Warehouse.id != w.id,
                                                Warehouse.is_default.is_(True))).scalars():
        o.is_default = False
    db.flush()
    w.is_default = True
    w.is_active = True


@router.patch("/warehouses/{warehouse_id}")
def update_warehouse(warehouse_id: str, body: WarehouseIn, db: Session = Depends(get_db),
                     p: Principal = Depends(require("inventory.adjust"))):
    w = get_warehouse(db, p.store.id, parse_uuid(warehouse_id, "Warehouse"))
    f = body.model_fields_set
    if body.name:
        w.name = body.name.strip()
    if body.code:
        code = body.code.strip().upper()
        if not CODE_RE.match(code):
            raise AppError("invalid_warehouse", "Code must be 2–20 characters (A–Z, 0–9, - _)")
        if code != w.code and db.execute(select(Warehouse.id).where(Warehouse.store_id == p.store.id,
                                                                    Warehouse.code == code)).first():
            raise Conflict("code_taken", f"Warehouse code {code} is already used")
        w.code = code
    if "address" in f:
        w.address = _clean_address(body.address)
    if "district" in f:
        w.district = body.district
    if body.is_active is False and w.is_active:
        if w.is_default:
            raise AppError("default_required", "Make another warehouse the default before deactivating this one")
        reserved = db.execute(select(func.coalesce(func.sum(StockLevel.reserved), 0)).where(
            StockLevel.warehouse_id == w.id)).scalar_one()
        if reserved:
            raise Conflict("has_reservations", f"{reserved} units are reserved here for open orders")
        w.is_active = False
    elif body.is_active:
        w.is_active = True
    if body.is_default:
        _make_default(db, p, w)
    elif body.is_default is False and w.is_default:
        raise AppError("default_required", "Choose another warehouse as the default instead")
    audit(db, p.store.id, p.actor, "warehouse.updated", "warehouse", w.id, {"fields": sorted(f)})
    db.commit()
    return warehouse_out(w)


@router.delete("/warehouses/{warehouse_id}")
def delete_warehouse(warehouse_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    w = get_warehouse(db, p.store.id, parse_uuid(warehouse_id, "Warehouse"))
    if w.is_default:
        raise AppError("default_required", "The default warehouse cannot be deleted")
    used = db.execute(select(StockMovement.id).where(StockMovement.warehouse_id == w.id).limit(1)).first() or \
        db.execute(select(PurchaseOrder.id).where(PurchaseOrder.warehouse_id == w.id).limit(1)).first()
    if used:
        raise Conflict("warehouse_in_use", "This warehouse has stock history. Deactivate it instead.")
    db.execute(text("DELETE FROM stock_levels WHERE warehouse_id = :w"), {"w": w.id})
    code = w.code
    db.delete(w)
    audit(db, p.store.id, p.actor, "warehouse.deleted", "warehouse", warehouse_id, {"code": code})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ stock overview
def _state(tracked: bool, avail: int, reorder: int) -> str:
    if not tracked:
        return "untracked"
    if avail <= 0:
        return "out"
    return "low" if reorder > 0 and avail <= reorder else "ok"


@router.get("/stock")
def stock_overview(
    q: str = Query("", max_length=100), warehouse_id: uuid.UUID | None = None,
    state: str | None = Query(None, pattern="^(ok|low|out|untracked)$"), page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db), p: Principal = Depends(require("inventory.read")),
):
    sid = p.store.id
    lv = (select(StockLevel.variant_id.label("vid"), func.sum(StockLevel.on_hand).label("oh"),
                 func.sum(StockLevel.reserved).label("rs"), func.sum(StockLevel.reorder_point).label("ro"))
          .join(Warehouse, Warehouse.id == StockLevel.warehouse_id)
          .where(Warehouse.store_id == sid, Warehouse.is_active.is_(True)))
    if warehouse_id:
        lv = lv.where(StockLevel.warehouse_id == warehouse_id)
    lv = lv.group_by(StockLevel.variant_id).subquery("lv")
    oh, rs, ro = (func.coalesce(lv.c.oh, 0), func.coalesce(lv.c.rs, 0), func.coalesce(lv.c.ro, 0))
    base = (select(Variant, Product, oh.label("oh"), rs.label("rs"), ro.label("ro"))
            .join(Product, Product.id == Variant.product_id).outerjoin(lv, lv.c.vid == Variant.id)
            .where(Variant.store_id == sid, Variant.is_active.is_(True), Product.status != "archived"))
    if q.strip():
        pat = like(q.strip())
        base = base.where(Variant.sku.ilike(pat) | Product.title.ilike(pat) | (Variant.barcode == q.strip()))
    avail = oh - rs
    if state == "untracked":
        base = base.where(Variant.track_inventory.is_(False))
    elif state:
        base = base.where(Variant.track_inventory.is_(True))
        if state == "out":
            base = base.where(avail <= 0)
        elif state == "low":
            base = base.where(avail > 0, ro > 0, avail <= ro)
        else:
            base = base.where(avail > 0, (ro <= 0) | (avail > ro))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(Product.title, Variant.position, Variant.sku)
                      .limit(page_size).offset((page - 1) * page_size)).all()
    vids = [r[0].id for r in rows]
    per: dict = {}
    if vids:
        for lvl, wh in db.execute(select(StockLevel, Warehouse).join(Warehouse, Warehouse.id == StockLevel.warehouse_id)
                                  .where(StockLevel.variant_id.in_(vids), Warehouse.store_id == sid)
                                  .order_by(Warehouse.is_default.desc(), Warehouse.name)):
            per.setdefault(lvl.variant_id, []).append({**level_out(lvl), "warehouse": wh.name, "code": wh.code,
                                                       "warehouse_active": wh.is_active})
    items = [{"variant_id": str(v.id), "sku": v.sku, "variant_title": v.title, "product_id": str(pr.id),
              "product_title": pr.title, "track_inventory": v.track_inventory, "allow_backorder": v.allow_backorder,
              "on_hand": int(o), "reserved": int(r), "available": int(o) - int(r), "reorder_point": int(rr),
              "state": _state(v.track_inventory, int(o) - int(r), int(rr)), "is_demo": pr.is_demo,
              "levels": per.get(v.id, [])} for v, pr, o, r, rr in rows]
    return Page(items, total, page, page_size).dump()


@router.get("/low-stock")
def low_stock(db: Session = Depends(get_db), p: Principal = Depends(require("inventory.read"))):
    rows = db.execute(low_stock_rows_query(p.store.id).order_by(
        (StockLevel.on_hand - StockLevel.reserved), Product.title).limit(500)).all()
    return {"items": [{"variant_id": str(v.id), "sku": v.sku, "variant_title": v.title, "product_id": str(pr.id),
                       "product_title": pr.title, "warehouse_id": str(w.id), "warehouse": w.name,
                       "available": lv.on_hand - lv.reserved, "on_hand": lv.on_hand, "reserved": lv.reserved,
                       "reorder_point": lv.reorder_point,
                       "suggested_reorder": max(lv.reorder_point * 2 - (lv.on_hand - lv.reserved), 1)}
                      for lv, v, pr, w in rows]}


# ------------------------------------------------------------------ stock operations
class AdjustIn(BaseModel):
    variant_id: uuid.UUID
    warehouse_id: uuid.UUID
    delta: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = "adjust"
    note: str | None = Field(default=None, max_length=500)


class ReceiveIn(BaseModel):
    variant_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: int = Field(gt=0, le=1_000_000)
    note: str | None = Field(default=None, max_length=500)


class TransferIn(BaseModel):
    variant_id: uuid.UUID
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    quantity: int = Field(gt=0, le=1_000_000)
    note: str | None = Field(default=None, max_length=500)


class CountIn(BaseModel):
    variant_id: uuid.UUID
    warehouse_id: uuid.UUID
    counted: int = Field(ge=0, le=10_000_000)
    note: str | None = Field(default=None, max_length=500)


class ReorderIn(BaseModel):
    variant_id: uuid.UUID
    warehouse_id: uuid.UUID
    reorder_point: int = Field(ge=0, le=1_000_000)


@router.get("/reasons")
def reasons(p: Principal = Depends(require("inventory.read"))):
    return {"items": [{"code": k, "label": v} for k, v in ADJUST_REASONS.items()]}


@router.post("/adjust")
def adjust(body: AdjustIn, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    lv = adjust_stock(db, p, body.variant_id, body.warehouse_id, body.delta, body.reason, body.note)
    audit(db, p.store.id, p.actor, "inventory.adjusted", "variant", body.variant_id,
          {"warehouse_id": str(body.warehouse_id), "delta": body.delta, "reason": body.reason, "note": body.note,
           "on_hand": lv.on_hand})
    out = level_out(lv)
    db.commit()
    return out


@router.post("/receive")
def receive(body: ReceiveIn, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    lv = receive_stock(db, p, body.variant_id, body.warehouse_id, body.quantity, body.note)
    audit(db, p.store.id, p.actor, "inventory.received", "variant", body.variant_id,
          {"warehouse_id": str(body.warehouse_id), "quantity": body.quantity, "note": body.note})
    out = level_out(lv)
    db.commit()
    return out


@router.post("/transfer")
def transfer(body: TransferIn, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    ref = transfer_stock(db, p, body.variant_id, body.from_warehouse_id, body.to_warehouse_id, body.quantity, body.note)
    audit(db, p.store.id, p.actor, "inventory.transferred", "variant", body.variant_id,
          {"from": str(body.from_warehouse_id), "to": str(body.to_warehouse_id), "quantity": body.quantity,
           "transfer_id": str(ref)})
    db.commit()
    return {"transfer_id": str(ref), "quantity": body.quantity}


@router.post("/count")
def count(body: CountIn, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    lv, delta = count_stock(db, p, body.variant_id, body.warehouse_id, body.counted, body.note)
    audit(db, p.store.id, p.actor, "inventory.counted", "variant", body.variant_id,
          {"warehouse_id": str(body.warehouse_id), "counted": body.counted, "delta": delta, "note": body.note})
    out = {**level_out(lv), "delta": delta}
    db.commit()
    return out


@router.put("/reorder-point")
def set_reorder(body: ReorderIn, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    get_variant(db, p.store.id, body.variant_id)
    get_warehouse(db, p.store.id, body.warehouse_id)
    lv = lock_levels(db, [(body.variant_id, body.warehouse_id)])[(body.variant_id, body.warehouse_id)]
    lv.reorder_point = body.reorder_point
    audit(db, p.store.id, p.actor, "inventory.reorder_point_set", "variant", body.variant_id,
          {"warehouse_id": str(body.warehouse_id), "reorder_point": body.reorder_point})
    out = level_out(lv)
    db.commit()
    return out


@router.get("/movements")
def movements(
    variant_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None, reason: str | None = Query(None, max_length=30),
    q: str = Query("", max_length=100), date_from: dt.date | None = None, date_to: dt.date | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
    p: Principal = Depends(require("inventory.read")),
):
    base = (select(StockMovement, Variant, Product, Warehouse)
            .join(Variant, Variant.id == StockMovement.variant_id).join(Product, Product.id == Variant.product_id)
            .join(Warehouse, Warehouse.id == StockMovement.warehouse_id).where(StockMovement.store_id == p.store.id))
    if variant_id:
        base = base.where(StockMovement.variant_id == variant_id)
    if warehouse_id:
        base = base.where(StockMovement.warehouse_id == warehouse_id)
    if reason:
        base = base.where(StockMovement.reason == reason)
    if q.strip():
        base = base.where(Variant.sku.ilike(like(q.strip())) | Product.title.ilike(like(q.strip())))
    if date_from:
        base = base.where(StockMovement.created_at >= dt.datetime.combine(date_from, dt.time.min, dt.UTC))
    if date_to:
        base = base.where(StockMovement.created_at < dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time.min, dt.UTC))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(StockMovement.id.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    actors = {u.id: u.email for u in db.execute(select(User).where(User.id.in_(
        {m.actor_id for m, *_ in rows if m.actor_id}))).scalars()} if rows else {}
    items = [{"id": m.id, "at": m.created_at.isoformat(), "sku": v.sku, "variant_id": str(v.id), "variant_title": v.title,
              "product_title": pr.title, "warehouse": w.name, "warehouse_code": w.code, "reason": m.reason,
              "delta_on_hand": m.delta_on_hand, "delta_reserved": m.delta_reserved, "balance_on_hand": m.balance_on_hand,
              "balance_reserved": m.balance_reserved, "ref_type": m.ref_type, "ref_id": m.ref_id, "note": m.note,
              "actor": actors.get(m.actor_id) if m.actor_id else None} for m, v, pr, w in rows]
    return Page(items, total, page, page_size).dump()


# ------------------------------------------------------------------ suppliers
class SupplierIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    contact_name: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=1000)
    lead_time_days: int | None = Field(default=None, ge=0, le=365)
    note: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


def supplier_out(s: Supplier, po_count: int = 0) -> dict:
    return {"id": str(s.id), "name": s.name, "contact_name": s.contact_name, "email": s.email, "phone": s.phone,
            "address": s.address, "lead_time_days": s.lead_time_days, "note": s.note, "is_active": s.is_active,
            "purchase_orders": po_count}


@router.get("/suppliers")
def list_suppliers(db: Session = Depends(get_db), p: Principal = Depends(require("inventory.read"))):
    counts = dict(db.execute(select(PurchaseOrder.supplier_id, func.count()).where(
        PurchaseOrder.store_id == p.store.id).group_by(PurchaseOrder.supplier_id)).all())
    rows = db.execute(select(Supplier).where(Supplier.store_id == p.store.id).order_by(Supplier.name)).scalars().all()
    return {"items": [supplier_out(s, counts.get(s.id, 0)) for s in rows]}


@router.post("/suppliers", status_code=201)
def create_supplier(body: SupplierIn, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    if not body.name:
        raise AppError("name_required", "Supplier name is required")
    if db.execute(select(Supplier.id).where(Supplier.store_id == p.store.id,
                                            func.lower(Supplier.name) == body.name.strip().lower())).first():
        raise Conflict("name_taken", "A supplier with this name already exists")
    s = Supplier(store_id=p.store.id, name=body.name.strip(), contact_name=body.contact_name or "", email=body.email or None,
                 phone=body.phone or None, address=body.address or "", lead_time_days=body.lead_time_days or 0,
                 note=body.note or "")
    db.add(s)
    db.flush()
    audit(db, p.store.id, p.actor, "supplier.created", "supplier", s.id, {"name": s.name})
    db.commit()
    return supplier_out(s)


def _supplier(db: Session, p: Principal, sid: str) -> Supplier:
    s = db.execute(select(Supplier).where(Supplier.id == parse_uuid(sid, "Supplier"),
                                          Supplier.store_id == p.store.id)).scalar_one_or_none()
    if s is None:
        raise NotFound("Supplier")
    return s


@router.patch("/suppliers/{supplier_id}")
def update_supplier(supplier_id: str, body: SupplierIn, db: Session = Depends(get_db),
                    p: Principal = Depends(require("inventory.adjust"))):
    s = _supplier(db, p, supplier_id)
    f = body.model_fields_set
    if body.name:
        s.name = body.name.strip()
    for k in ("contact_name", "address", "note"):
        if k in f and getattr(body, k) is not None:
            setattr(s, k, getattr(body, k))
    for k in ("email", "phone"):
        if k in f:
            setattr(s, k, getattr(body, k) or None)
    if body.lead_time_days is not None:
        s.lead_time_days = body.lead_time_days
    if body.is_active is not None:
        s.is_active = body.is_active
    audit(db, p.store.id, p.actor, "supplier.updated", "supplier", s.id, {"fields": sorted(f)})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("name_taken", "A supplier with this name already exists") from exc
    return supplier_out(s)


@router.delete("/suppliers/{supplier_id}")
def delete_supplier(supplier_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    s = _supplier(db, p, supplier_id)
    if db.execute(select(PurchaseOrder.id).where(PurchaseOrder.supplier_id == s.id).limit(1)).first():
        raise Conflict("supplier_in_use", "This supplier has purchase orders. Deactivate it instead.")
    name = s.name
    db.delete(s)
    audit(db, p.store.id, p.actor, "supplier.deleted", "supplier", supplier_id, {"name": name})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ purchase orders
class POLineIn(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(gt=0, le=1_000_000)
    unit_cost_minor: int = Field(default=0, ge=0, le=10**12)


class POIn(BaseModel):
    supplier_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    expected_at: dt.date | None = None
    lines: list[POLineIn] | None = Field(default=None, max_length=500)


class ReceiveLine(BaseModel):
    line_id: uuid.UUID
    quantity: int = Field(ge=0, le=1_000_000)


class POReceiveIn(BaseModel):
    lines: list[ReceiveLine] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)


def _po(db: Session, p: Principal, po_id: str, lock: bool = False) -> PurchaseOrder:
    q = select(PurchaseOrder).where(PurchaseOrder.id == parse_uuid(po_id, "Purchase order"),
                                    PurchaseOrder.store_id == p.store.id)
    po = db.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if po is None:
        raise NotFound("Purchase order")
    return po


def po_out(db: Session, p: Principal, po: PurchaseOrder, with_lines: bool = True) -> dict:
    sup = db.get(Supplier, po.supplier_id)
    wh = db.get(Warehouse, po.warehouse_id)
    lines = db.execute(select(PurchaseOrderLine, Variant, Product).join(Variant, Variant.id == PurchaseOrderLine.variant_id)
                       .join(Product, Product.id == Variant.product_id)
                       .where(PurchaseOrderLine.purchase_order_id == po.id).order_by(PurchaseOrderLine.id)).all()
    total = sum(ln.quantity * ln.unit_cost_minor for ln, *_ in lines)
    out = {"id": str(po.id), "number": po.number, "status": po.status, "supplier_id": str(po.supplier_id),
           "supplier": sup.name if sup else None, "warehouse_id": str(po.warehouse_id), "warehouse": wh.name if wh else None,
           "reference": po.reference, "note": po.note, "expected_at": po.expected_at.isoformat() if po.expected_at else None,
           "ordered_at": po.ordered_at.isoformat() if po.ordered_at else None,
           "received_at": po.received_at.isoformat() if po.received_at else None, "created_at": po.created_at.isoformat(),
           "total_minor": total, "currency": p.store.currency, "units": sum(ln.quantity for ln, *_ in lines),
           "units_received": sum(ln.received_quantity for ln, *_ in lines)}
    if with_lines:
        out["lines"] = [{"id": str(ln.id), "variant_id": str(v.id), "sku": v.sku, "product_title": pr.title,
                         "variant_title": v.title, "quantity": ln.quantity, "received_quantity": ln.received_quantity,
                         "unit_cost_minor": ln.unit_cost_minor} for ln, v, pr in lines]
    return out


def _set_lines(db: Session, p: Principal, po: PurchaseOrder, lines: list[POLineIn]) -> None:
    seen = set()
    for ln in lines:
        if ln.variant_id in seen:
            raise AppError("duplicate_line", "Each variant can appear once per purchase order")
        seen.add(ln.variant_id)
        get_variant(db, p.store.id, ln.variant_id)
    db.query(PurchaseOrderLine).filter(PurchaseOrderLine.purchase_order_id == po.id).delete()
    for ln in lines:
        db.add(PurchaseOrderLine(purchase_order_id=po.id, variant_id=ln.variant_id, quantity=ln.quantity,
                                 unit_cost_minor=ln.unit_cost_minor))


@router.get("/purchase-orders")
def list_pos(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
             db: Session = Depends(get_db), p: Principal = Depends(require("inventory.read"))):
    q = select(PurchaseOrder).where(PurchaseOrder.store_id == p.store.id)
    if status:
        q = q.where(PurchaseOrder.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(PurchaseOrder.created_at.desc()).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return Page([po_out(db, p, r, with_lines=False) for r in rows], total, page, page_size).dump()


@router.post("/purchase-orders", status_code=201)
def create_po(body: POIn, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    if not body.supplier_id or not body.warehouse_id:
        raise AppError("po_incomplete", "Choose a supplier and a receiving warehouse")
    sup = _supplier(db, p, str(body.supplier_id))
    if not sup.is_active:
        raise AppError("supplier_inactive", "This supplier is inactive")
    get_warehouse(db, p.store.id, body.warehouse_id)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"po:{p.store.id}"})
    n = db.execute(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.store_id == p.store.id)).scalar_one()
    po = PurchaseOrder(store_id=p.store.id, number=f"PO-{n + 1:06d}", supplier_id=sup.id, warehouse_id=body.warehouse_id,
                       reference=body.reference or "", note=body.note or "", expected_at=body.expected_at)
    db.add(po)
    db.flush()
    _set_lines(db, p, po, body.lines or [])
    audit(db, p.store.id, p.actor, "purchase_order.created", "purchase_order", po.id, {"number": po.number})
    db.commit()
    return po_out(db, p, po)


@router.get("/purchase-orders/{po_id}")
def get_po(po_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.read"))):
    return po_out(db, p, _po(db, p, po_id))


@router.patch("/purchase-orders/{po_id}")
def update_po(po_id: str, body: POIn, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    po = _po(db, p, po_id, lock=True)
    if po.status != "draft":
        raise Conflict("po_locked", "Only draft purchase orders can be edited")
    f = body.model_fields_set
    if body.supplier_id:
        po.supplier_id = _supplier(db, p, str(body.supplier_id)).id
    if body.warehouse_id:
        po.warehouse_id = get_warehouse(db, p.store.id, body.warehouse_id).id
    for k in ("reference", "note"):
        if k in f and getattr(body, k) is not None:
            setattr(po, k, getattr(body, k))
    if "expected_at" in f:
        po.expected_at = body.expected_at
    if body.lines is not None:
        _set_lines(db, p, po, body.lines)
    audit(db, p.store.id, p.actor, "purchase_order.updated", "purchase_order", po.id, {"number": po.number})
    db.commit()
    return po_out(db, p, po)


@router.post("/purchase-orders/{po_id}/order")
def place_po(po_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    po = _po(db, p, po_id, lock=True)
    if po.status != "draft":
        raise Conflict("po_locked", "Only draft purchase orders can be placed")
    if not db.execute(select(PurchaseOrderLine.id).where(PurchaseOrderLine.purchase_order_id == po.id).limit(1)).first():
        raise AppError("po_empty", "Add at least one line before placing the order")
    po.status, po.ordered_at = "ordered", utcnow()
    audit(db, p.store.id, p.actor, "purchase_order.ordered", "purchase_order", po.id, {"number": po.number})
    db.commit()
    return po_out(db, p, po)


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_po(po_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("inventory.adjust"))):
    po = _po(db, p, po_id, lock=True)
    if po.status not in ("draft", "ordered"):
        raise Conflict("po_locked", "Only draft or ordered purchase orders without receipts can be cancelled")
    po.status = "cancelled"
    audit(db, p.store.id, p.actor, "purchase_order.cancelled", "purchase_order", po.id, {"number": po.number})
    db.commit()
    return po_out(db, p, po)


@router.post("/purchase-orders/{po_id}/receive")
def receive_po(po_id: str, body: POReceiveIn, db: Session = Depends(get_db),
               p: Principal = Depends(require("inventory.adjust"))):
    po = _po(db, p, po_id, lock=True)
    if po.status not in ("ordered", "partially_received"):
        raise Conflict("po_not_receivable", "Place the order before receiving stock")
    lines = {ln.id: ln for ln in db.execute(select(PurchaseOrderLine).where(
        PurchaseOrderLine.purchase_order_id == po.id).with_for_update()).scalars()}
    todo = [(lines.get(r.line_id), r.quantity) for r in body.lines if r.quantity > 0]
    if not todo:
        raise AppError("nothing_to_receive", "Enter a quantity to receive")
    for ln, qty in todo:
        if ln is None:
            raise NotFound("Purchase order line")
        if qty > ln.quantity - ln.received_quantity:
            raise Conflict("over_receipt", f"Only {ln.quantity - ln.received_quantity} units are still expected on this line")
    for ln, qty in todo:
        receive_stock(db, p, ln.variant_id, po.warehouse_id, qty, body.note or f"Received against {po.number}",
                      ref_type="purchase_order", ref_id=str(po.id))
        ln.received_quantity += qty
    done = all(ln.received_quantity >= ln.quantity for ln in lines.values())
    po.status = "received" if done else "partially_received"
    if done:
        po.received_at = utcnow()
    audit(db, p.store.id, p.actor, "purchase_order.received", "purchase_order", po.id,
          {"number": po.number, "units": sum(q for _, q in todo), "status": po.status})
    db.commit()
    return po_out(db, p, po)
