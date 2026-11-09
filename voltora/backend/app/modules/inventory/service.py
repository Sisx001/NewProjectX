"""Inventory domain: stock operations built on ledger.apply_movement, low-stock queries, seed and shell hooks."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.deps import Principal
from ...core.errors import AppError, Conflict, NotFound
from ...core.services import NAV_COUNTERS, event_type
from ...seed import seed_hook
from ..catalog.models import Product, Variant
from .ledger import apply_movement, lock_levels
from .models import StockLevel, Warehouse

EV_STOCK_LOW = event_type("inventory.low_stock", "A variant fell to or below its reorder point")

POSITIVE_REASONS = {"receive", "return", "adjust"}
ADJUST_REASONS = {"adjust": "Stock correction", "damage": "Damaged or spoiled", "return": "Customer return restocked",
                  "loss": "Lost or stolen"}
# "loss" is recorded in the ledger under the existing "damage" reason code with a note prefix.


def get_warehouse(db: Session, store_id, wid) -> Warehouse:
    w = db.execute(select(Warehouse).where(Warehouse.id == wid, Warehouse.store_id == store_id)).scalar_one_or_none()
    if w is None:
        raise NotFound("Warehouse")
    return w


def get_variant(db: Session, store_id, vid) -> Variant:
    v = db.execute(select(Variant).where(Variant.id == vid, Variant.store_id == store_id)).scalar_one_or_none()
    if v is None:
        raise NotFound("Variant")
    return v


def adjust_stock(db: Session, p: Principal, variant_id, warehouse_id, delta: int, reason: str, note: str | None) -> StockLevel:
    if delta == 0:
        raise AppError("zero_adjustment", "Enter a quantity other than zero")
    if reason not in ADJUST_REASONS:
        raise AppError("invalid_reason", "Unknown reason code")
    if reason in ("damage", "loss") and delta > 0:
        raise AppError("invalid_quantity", "Damage and loss reduce stock; enter a negative quantity")
    if reason == "return" and delta < 0:
        raise AppError("invalid_quantity", "Returned items add stock; enter a positive quantity")
    get_variant(db, p.store.id, variant_id)
    get_warehouse(db, p.store.id, warehouse_id)
    level = lock_levels(db, [(variant_id, warehouse_id)])[(variant_id, warehouse_id)]
    if delta < 0 and level.on_hand + delta < level.reserved:
        raise Conflict("insufficient_stock", f"Only {level.on_hand - level.reserved} unreserved units can be removed",
                       {"available": level.on_hand - level.reserved})
    ledger_reason = "damage" if reason == "loss" else reason
    text = f"[loss] {note}" if reason == "loss" and note else (note or None)
    return apply_movement(db, store_id=p.store.id, variant_id=variant_id, warehouse_id=warehouse_id,
                          reason=ledger_reason, delta_on_hand=delta, note=text, actor_id=p.actor.id, level=level)


def receive_stock(db: Session, p: Principal, variant_id, warehouse_id, quantity: int, note: str | None,
                  ref_type: str | None = None, ref_id: str | None = None) -> StockLevel:
    if quantity <= 0:
        raise AppError("invalid_quantity", "Quantity must be positive")
    get_variant(db, p.store.id, variant_id)
    get_warehouse(db, p.store.id, warehouse_id)
    return apply_movement(db, store_id=p.store.id, variant_id=variant_id, warehouse_id=warehouse_id, reason="receive",
                          delta_on_hand=quantity, note=note, ref_type=ref_type, ref_id=ref_id, actor_id=p.actor.id)


def transfer_stock(db: Session, p: Principal, variant_id, src, dst, quantity: int, note: str | None) -> uuid.UUID:
    if quantity <= 0:
        raise AppError("invalid_quantity", "Quantity must be positive")
    if src == dst:
        raise AppError("same_warehouse", "Choose two different warehouses")
    get_variant(db, p.store.id, variant_id)
    get_warehouse(db, p.store.id, src)
    get_warehouse(db, p.store.id, dst)
    levels = lock_levels(db, [(variant_id, src), (variant_id, dst)])
    a = levels[(variant_id, src)]
    if a.on_hand - a.reserved < quantity:
        raise Conflict("insufficient_stock", f"Only {max(a.on_hand - a.reserved, 0)} unreserved units available to move",
                       {"available": max(a.on_hand - a.reserved, 0)})
    ref = uuid.uuid4()
    apply_movement(db, store_id=p.store.id, variant_id=variant_id, warehouse_id=src, reason="transfer_out",
                   delta_on_hand=-quantity, ref_type="transfer", ref_id=str(ref), note=note, actor_id=p.actor.id, level=a)
    apply_movement(db, store_id=p.store.id, variant_id=variant_id, warehouse_id=dst, reason="transfer_in",
                   delta_on_hand=quantity, ref_type="transfer", ref_id=str(ref), note=note, actor_id=p.actor.id,
                   level=levels[(variant_id, dst)])
    return ref


def count_stock(db: Session, p: Principal, variant_id, warehouse_id, counted: int, note: str | None) -> tuple[StockLevel, int]:
    if counted < 0:
        raise AppError("invalid_quantity", "Counted quantity cannot be negative")
    get_variant(db, p.store.id, variant_id)
    get_warehouse(db, p.store.id, warehouse_id)
    level = lock_levels(db, [(variant_id, warehouse_id)])[(variant_id, warehouse_id)]
    if counted < level.reserved:
        raise Conflict("below_reserved", f"{level.reserved} units are reserved for open orders; the count cannot be lower",
                       {"reserved": level.reserved})
    delta = counted - level.on_hand
    if delta == 0:
        return level, 0
    return apply_movement(db, store_id=p.store.id, variant_id=variant_id, warehouse_id=warehouse_id, reason="count",
                          delta_on_hand=delta, note=note, actor_id=p.actor.id, level=level), delta


def low_stock_rows_query(store_id):
    """(StockLevel, Variant, Product, Warehouse) rows at or below reorder point (reorder_point > 0, tracked, live)."""
    return (select(StockLevel, Variant, Product, Warehouse)
            .join(Variant, Variant.id == StockLevel.variant_id).join(Product, Product.id == Variant.product_id)
            .join(Warehouse, Warehouse.id == StockLevel.warehouse_id)
            .where(Variant.store_id == store_id, Variant.is_active.is_(True), Variant.track_inventory.is_(True),
                   Product.status != "archived", Warehouse.is_active.is_(True), StockLevel.reorder_point > 0,
                   StockLevel.on_hand - StockLevel.reserved <= StockLevel.reorder_point))


def low_stock_count(db: Session, store_id) -> int:
    sub = low_stock_rows_query(store_id).with_only_columns(StockLevel.variant_id).distinct().subquery()
    return db.execute(select(func.count()).select_from(sub)).scalar_one()


@seed_hook(20, "inventory_default_warehouse")
def seed_default_warehouse(db, store, demo: bool):
    if db.execute(select(Warehouse.id).where(Warehouse.store_id == store.id).limit(1)).first():
        return {}
    db.add(Warehouse(store_id=store.id, name="Main warehouse", code="MAIN", is_default=True))
    db.flush()
    return {"warehouse": "MAIN"}


def _nav_counter(db: Session, p: Principal) -> dict:
    if not p.can("inventory.read"):
        return {}
    return {"inventory": low_stock_count(db, p.store.id)}


NAV_COUNTERS.append(_nav_counter)
