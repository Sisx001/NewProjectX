"""Order state machine and the order-side primitives shared by checkout, payments and shipping.

Every status change goes through `transition()`; the allowed table below is the only definition."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.errors import Conflict, NotFound
from ...core.security import consteq, hmac_sha256, token_hash
from ...core.services import SYSTEM, Actor, audit, emit, event_type, setting
from ...db import utcnow
from .models import Order, OrderEvent, OrderLine

EV_ORDER_PLACED = event_type("order.placed", "A customer placed an order")
EV_ORDER_STATUS = event_type("order.status_changed", "An order moved to a new status")
EV_ORDER_CANCELLED = event_type("order.cancelled", "An order was cancelled (reservations released)")
EV_ORDER_PAID = event_type("order.paid", "An order's payment was confirmed")
EV_ORDER_REFUNDED = event_type("order.refunded", "Money was refunded on an order")

ORDER_TRANSITIONS: dict[str, set[str]] = {
    "awaiting_payment": {"confirmed", "on_hold", "cancelled"},
    "pending": {"confirmed", "on_hold", "cancelled"},
    "confirmed": {"partially_shipped", "shipped", "on_hold", "cancelled"},
    "on_hold": {"pending", "awaiting_payment", "confirmed", "cancelled"},
    "partially_shipped": {"shipped", "delivered", "returned"},
    "shipped": {"partially_shipped", "delivered", "returned"},
    "delivered": set(),
    "returned": set(),
    "cancelled": set(),
}
ORDER_STATUSES = list(ORDER_TRANSITIONS)
OPEN_STATUSES = ("awaiting_payment", "pending", "confirmed", "on_hold", "partially_shipped")
STATUS_LABELS = {
    "awaiting_payment": "Awaiting payment", "pending": "Pending confirmation", "confirmed": "Confirmed",
    "on_hold": "On hold", "partially_shipped": "Partially shipped", "shipped": "Shipped", "delivered": "Delivered",
    "returned": "Returned to sender", "cancelled": "Cancelled",
}
CUSTOMER_EMAIL_STATUSES = {"confirmed", "shipped", "delivered", "cancelled", "returned"}

AUTO_CONFIRM_COD = setting(key="checkout.auto_confirm_cod", type="bool", default=False, group="Checkout",
                           label="Auto-confirm cash-on-delivery orders",
                           description="When off, COD orders wait in 'Pending confirmation' until staff confirm them.",
                           write_permission="orders.write")
UNPAID_EXPIRY = setting(key="checkout.unpaid_expiry_minutes", type="int", default=60, min=5, max=10080,
                        group="Checkout", label="Cancel unpaid online orders after (minutes)",
                        description="Orders paid through an online gateway are cancelled and their stock released "
                                    "if no verified payment arrives in time.")
BANK_EXPIRY = setting(key="checkout.bank_transfer_expiry_hours", type="int", default=48, min=1, max=720,
                      group="Checkout", label="Cancel unpaid bank-transfer orders after (hours)")


def order_access_token(order: Order) -> str:
    """Derivable server-side only (secret key + order id + idempotency key); stored as a hash for lookups."""
    return hmac_sha256(get_settings().secret_key, f"order-access:{order.id}:{order.idempotency_key}")[:40]


def check_access_token(order: Order, token: str | None) -> bool:
    return bool(token) and consteq(token_hash(token), order.access_token_hash)


def get_order(db: Session, store_id: uuid.UUID, number: str, lock: bool = False) -> Order:
    q = select(Order).where(Order.store_id == store_id, Order.number == number.strip().upper())
    if lock:
        q = q.with_for_update()
    o = db.execute(q).scalar_one_or_none()
    if o is None:
        raise NotFound("Order")
    return o


def lines_of(db: Session, order_id: uuid.UUID, lock: bool = False) -> list[OrderLine]:
    q = select(OrderLine).where(OrderLine.order_id == order_id).order_by(OrderLine.id)
    if lock:
        q = q.with_for_update()
    return list(db.execute(q).scalars())


def timeline(db: Session, order: Order, kind: str, message: str, *, public: bool = False, actor: Actor = SYSTEM,
             data: dict | None = None) -> None:
    db.add(OrderEvent(order_id=order.id, kind=kind, message=message, public=public, actor_kind=actor.kind,
                      actor_id=actor.id, actor_label=actor.label, data=data or {}))


def customer_email(db: Session, order: Order, template: str, **extra) -> None:
    if not order.email:
        return
    from ..messaging.service import queue_message

    link = f"{get_settings().public_url.rstrip('/')}/order/{order.number}?token={order_access_token(order)}"
    ctx = {"name": order.name, "order_number": order.number, "link": link,
           "status": STATUS_LABELS.get(order.status, order.status), **extra}
    queue_message(db, order.store_id, channel="email", to=order.email, template=template, context=ctx,
                  related={"order_id": str(order.id), "order_number": order.number}, sensitive=True)


def transition(db: Session, order: Order, to: str, actor: Actor = SYSTEM, note: str | None = None,
               *, notify_customer: bool = True) -> None:
    """The single gate for order status changes. Raises 409 invalid_transition."""
    frm = order.status
    if to == frm:
        return
    if to not in ORDER_TRANSITIONS.get(frm, set()):
        raise Conflict("invalid_transition", f"An order that is {STATUS_LABELS.get(frm, frm).lower()} cannot move to "
                                             f"{STATUS_LABELS.get(to, to).lower()}",
                       details={"from": frm, "to": to, "allowed": sorted(ORDER_TRANSITIONS.get(frm, set()))})
    now = utcnow()
    if to == "on_hold":
        order.held_from = frm
        order.hold_reason = note
    if frm == "on_hold":
        if order.held_from and to not in ("cancelled", order.held_from, "confirmed"):
            raise Conflict("invalid_transition", f"Resume this order to {order.held_from} or confirm/cancel it",
                           details={"from": frm, "to": to})
        order.held_from = None
        order.hold_reason = None
    if to == "confirmed" and not order.confirmed_at:
        order.confirmed_at = now
    if to == "cancelled":
        order.cancelled_at = now
        order.cancel_reason = note
        order.expires_at = None
    if to == "delivered":
        order.delivered_at = now
    order.status = to
    msg = f"Status changed to {STATUS_LABELS.get(to, to)}" + (f" — {note}" if note else "")
    timeline(db, order, "status", msg, public=to != "on_hold", actor=actor, data={"from": frm, "to": to})
    emit(db, order.store_id, EV_ORDER_STATUS, {"order_id": str(order.id), "number": order.number, "from": frm,
                                                "to": to})
    if notify_customer and to in CUSTOMER_EMAIL_STATUSES and to != "cancelled":
        customer_email(db, order, "order.status")


def release_reservations(db: Session, order: Order, lines: list[OrderLine], actor: Actor, reason: str) -> int:
    from ..inventory.ledger import apply_movement, lock_levels

    keys = [(ln.variant_id, ln.warehouse_id) for ln in lines if ln.reserved_qty > 0 and ln.warehouse_id
            and ln.variant_id]
    levels = lock_levels(db, keys) if keys else {}
    released = 0
    for ln in lines:
        if ln.reserved_qty > 0 and ln.warehouse_id and ln.variant_id:
            apply_movement(db, store_id=order.store_id, variant_id=ln.variant_id, warehouse_id=ln.warehouse_id,
                           reason="release", delta_reserved=-ln.reserved_qty, ref_type="order", ref_id=order.number,
                           note=reason, actor_id=actor.id, level=levels[(ln.variant_id, ln.warehouse_id)])
            released += ln.reserved_qty
            ln.reserved_qty = 0
    return released


def cancel_order(db: Session, order: Order, actor: Actor, reason: str, *, by_customer: bool = False,
                 notify: bool = True) -> None:
    """Cancel, release stock reservations, void open payment intents and return discount usage. Caller commits."""
    from ..discounts.service import release_for_order
    from ..payments.models import PaymentIntent

    if order.paid_minor - order.refunded_minor > 0:
        raise Conflict("refund_required", "This order has captured payment. Refund it before cancelling.",
                       details={"refundable_minor": order.paid_minor - order.refunded_minor})
    if any(ln.fulfilled_qty - ln.returned_qty > 0 for ln in lines_of(db, order.id)):
        raise Conflict("already_shipped", "Items from this order have shipped; handle it as a return instead")
    transition(db, order, "cancelled", actor, reason, notify_customer=False)
    lines = lines_of(db, order.id, lock=True)
    released = release_reservations(db, order, lines, actor, f"Order cancelled: {reason}")
    for intent in db.execute(select(PaymentIntent).where(
            PaymentIntent.order_id == order.id,
            PaymentIntent.status.in_(["pending", "requires_action", "processing", "awaiting_verification"]))
            .with_for_update()).scalars():
        intent.status = "cancelled"
    if order.payment_status in ("unpaid", "awaiting_verification", "failed"):
        order.payment_status = "voided"
    discount_returned = release_for_order(db, order.id, f"order {order.number} cancelled")
    if order.customer_id:
        from ..customers.models import Customer

        c = db.get(Customer, order.customer_id, with_for_update=True)
        if c:
            c.orders_count = max(0, c.orders_count - 1)
            c.total_spent_minor = max(0, c.total_spent_minor - order.total_minor)
    timeline(db, order, "stock", f"Released {released} reserved unit(s)" +
             ("; discount usage returned" if discount_returned else ""), actor=actor)
    emit(db, order.store_id, EV_ORDER_CANCELLED, {"order_id": str(order.id), "number": order.number,
                                                   "reason": reason, "by_customer": by_customer})
    audit(db, order.store_id, actor, "orders.cancelled", "order", order.id,
          {"number": order.number, "reason": reason, "released_units": released})
    if notify:
        customer_email(db, order, "order.status")


def recompute_payment_status(order: Order) -> None:
    if order.refunded_minor > 0:
        order.payment_status = "refunded" if order.refunded_minor >= order.paid_minor else "partially_refunded"
    elif order.paid_minor >= order.total_minor and order.total_minor > 0:
        order.payment_status = "paid"
    elif order.paid_minor > 0:
        order.payment_status = "partially_paid"


def recompute_fulfillment(db: Session, order: Order, actor: Actor = SYSTEM) -> None:
    """Derive fulfillment_status + order status from lines and shipments (called after every shipment change)."""
    from ..shipping.models import Shipment

    lines = lines_of(db, order.id)
    ships = list(db.execute(select(Shipment).where(Shipment.order_id == order.id,
                                                   Shipment.status != "cancelled")).scalars())
    qty = sum(ln.quantity for ln in lines)
    shipped = sum(ln.fulfilled_qty for ln in lines)
    if not ships:
        order.fulfillment_status = "unfulfilled"
        return
    delivered = [s for s in ships if s.status == "delivered"]
    returned = [s for s in ships if s.status == "returned"]
    returning = [s for s in ships if s.status in ("returning", "failed_attempt")]
    if returned and len(returned) == len(ships):
        fs, status = "returned", "returned"
    elif delivered and len(delivered) + len(returned) == len(ships) and shipped >= qty:
        fs, status = "delivered", "delivered"
    elif returning:
        fs, status = "returning", None
    elif shipped >= qty:
        fs, status = "shipped", "shipped"
    else:
        fs, status = "partially_shipped", "partially_shipped"
    order.fulfillment_status = fs
    if status and status != order.status and status in ORDER_TRANSITIONS.get(order.status, set()):
        transition(db, order, status, actor)
