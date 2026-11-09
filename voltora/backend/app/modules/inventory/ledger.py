"""The only code path allowed to change stock_levels. Row locks make concurrent checkouts unable to oversell."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...core.errors import Conflict
from .models import StockLevel, StockMovement


class InsufficientStock(Conflict):
    def __init__(self, variant_id, available: int, requested: int):
        super().__init__("insufficient_stock", f"Only {max(available, 0)} left in stock",
                         details={"variant_id": str(variant_id), "available": max(available, 0),
                                  "requested": requested})


def lock_levels(db: Session, keys: list[tuple[uuid.UUID, uuid.UUID]]) -> dict[tuple, StockLevel]:
    """Lock (variant, warehouse) rows in a stable order to avoid deadlocks; creates missing rows at zero."""
    keys = sorted(set(keys), key=lambda k: (str(k[0]), str(k[1])))
    for v, w in keys:
        db.execute(insert(StockLevel).values(variant_id=v, warehouse_id=w, on_hand=0, reserved=0)
                   .on_conflict_do_nothing())
    out = {}
    for v, w in keys:
        out[(v, w)] = db.execute(select(StockLevel).where(StockLevel.variant_id == v, StockLevel.warehouse_id == w)
                                 .with_for_update()).scalar_one()
    return out


def apply_movement(db: Session, *, store_id: uuid.UUID, variant_id: uuid.UUID, warehouse_id: uuid.UUID, reason: str,
                   delta_on_hand: int = 0, delta_reserved: int = 0, ref_type: str | None = None,
                   ref_id: str | None = None, note: str | None = None, actor_id: uuid.UUID | None = None,
                   allow_negative_available: bool = False, level: StockLevel | None = None) -> StockLevel:
    """Apply one movement atomically (caller owns the transaction).

    Invariants: on_hand >= 0, reserved >= 0 and, unless backorders are allowed, reserved <= on_hand.
    """
    if level is None:
        level = lock_levels(db, [(variant_id, warehouse_id)])[(variant_id, warehouse_id)]
    new_on_hand = level.on_hand + delta_on_hand
    new_reserved = level.reserved + delta_reserved
    if new_on_hand < 0:
        raise InsufficientStock(variant_id, level.on_hand - level.reserved, -delta_on_hand)
    if new_reserved < 0:
        raise Conflict("reservation_underflow", "Cannot release more stock than is reserved")
    if not allow_negative_available and new_reserved > new_on_hand and delta_reserved > 0:
        raise InsufficientStock(variant_id, level.on_hand - level.reserved, delta_reserved)
    level.on_hand, level.reserved = new_on_hand, new_reserved
    db.add(StockMovement(store_id=store_id, variant_id=variant_id, warehouse_id=warehouse_id, reason=reason,
                         delta_on_hand=delta_on_hand, delta_reserved=delta_reserved, balance_on_hand=new_on_hand,
                         balance_reserved=new_reserved, ref_type=ref_type, ref_id=ref_id, note=note,
                         actor_id=actor_id))
    return level


def available(level: StockLevel | None) -> int:
    return 0 if level is None else level.on_hand - level.reserved
