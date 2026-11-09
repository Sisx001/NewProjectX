"""Zones/rates quoting, shipment booking, courier tracking events, RTO restock and COD reconciliation."""
from __future__ import annotations

import json
import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.errors import AppError, Conflict, NotFound
from ...core.services import SYSTEM, Actor, audit, emit, event_type
from ...db import utcnow
from ...seed import seed_hook
from ..commerce.models import Order
from ..commerce.orders import lines_of, recompute_fulfillment, timeline
from ..payments.providers.base import (
    SIGNATURE_HEADER,
    InvalidSignature,
    ProviderContext,
    ensure_secret,
    sign,
)
from . import locations
from .couriers import COURIERS
from .couriers.base import SHIPMENT_LABELS, SHIPMENT_TRANSITIONS, CourierEvent
from .couriers.drafts import CATALOG as COURIER_CATALOG
from .couriers.sandbox import SANDBOX_COURIER_SECRET_REF
from .models import CourierAccount, Shipment, ShipmentEvent, ShippingRate, ShippingZone

log = logging.getLogger("voltora.shipping")
EV_SHIPMENT_STATUS = event_type("shipment.status_changed", "A shipment moved to a new courier status")
EV_SHIPMENT_FAILED = event_type("shipment.delivery_failed", "A delivery attempt failed")

DHAKA_METRO = ["Demra", "Dhanmondi", "Gulshan", "Jatrabari", "Kalabagan", "Khilgaon", "Khilkhet", "Lalbag", "Mirpur",
               "Mohammadpur", "Motijheel", "Palton", "Ramna", "Sabujbag", "Sutrapur", "Tejgaon",
               "Tejgaon Industrial Area", "Uttara"]
DEFAULT_ZONES = [
    ("inside_dhaka", "Inside Dhaka", [{"district": "Dhaka", "upazila": u} for u in DHAKA_METRO], 8000, "1–2 days"),
    ("dhaka_suburbs", "Dhaka suburbs", [{"district": d} for d in ("Dhaka", "Gazipur", "Narayanganj")], 11000,
     "2–3 days"),
    ("outside_dhaka", "Outside Dhaka", [], 15000, "3–5 days"),
]


@seed_hook(40, "shipping")
def seed_shipping(db: Session, store, demo: bool) -> dict:
    created = 0
    for pos, (key, name, regions, fee, eta) in enumerate(DEFAULT_ZONES):
        z = db.execute(select(ShippingZone).where(ShippingZone.store_id == store.id, ShippingZone.seed_key == key)
                       ).scalar_one_or_none()
        if z is None:
            z = ShippingZone(store_id=store.id, name=name, regions=regions, position=pos, seed_key=key)
            db.add(z)
            db.flush()
            db.add(ShippingRate(zone_id=z.id, name="Standard delivery", kind="flat", amount_minor=fee,
                                cod_allowed=True, cod_fee_minor=0, eta_label=eta, is_seed_default=True))
            created += 1
    acc = db.execute(select(CourierAccount).where(CourierAccount.store_id == store.id,
                                                  CourierAccount.courier_key == "sandbox")).scalar_one_or_none()
    if acc is None:
        db.add(CourierAccount(store_id=store.id, courier_key="sandbox", label="Sandbox courier (simulation)",
                              mode="sandbox", enabled=get_settings().payments_mode == "sandbox",
                              verification={"state": "simulation"}))
    db.flush()
    return {"zones_created": created}


# ---------------------------------------------------------------- zones & quotes
def _specificity(region: dict, loc: dict) -> int:
    """-1 = no match; otherwise higher is more specific."""
    score = 0
    for level, weight in (("division", 1), ("district", 2), ("upazila", 4)):
        want = (region.get(level) or "").strip().lower()
        if not want:
            continue
        have = (loc.get(level) or "").strip().lower()
        if want != have:
            return -1
        score += weight
    return score


def match_zone(db: Session, store_id: uuid.UUID, address: dict) -> ShippingZone | None:
    loc = locations.resolve(address.get("district"), address.get("upazila")) or {}
    best, best_score = None, -2
    zones = db.execute(select(ShippingZone).where(ShippingZone.store_id == store_id, ShippingZone.active.is_(True))
                       .order_by(ShippingZone.position, ShippingZone.created_at)).scalars()
    for z in zones:
        score = 0 if not z.regions else max((_specificity(r, loc) for r in z.regions), default=-1)
        if score > best_score:
            best, best_score = z, score
    return best if best_score >= 0 else None


def rate_amount(rate: ShippingRate, merch_minor: int, weight_grams: int, free_shipping: bool) -> int:
    if free_shipping or (rate.free_over_minor is not None and merch_minor >= rate.free_over_minor):
        return 0
    amount = rate.amount_minor
    if rate.kind == "weight" and weight_grams > rate.base_weight_grams:
        extra_kg = -(-(weight_grams - rate.base_weight_grams) // 1000)
        amount += extra_kg * rate.per_kg_minor
    return amount


def shipping_options(db: Session, store_id: uuid.UUID, address: dict, merch_minor: int, weight_grams: int,
                     free_shipping: bool = False) -> list[dict]:
    zone = match_zone(db, store_id, address)
    if zone is None:
        return []
    rates = db.execute(select(ShippingRate).where(ShippingRate.zone_id == zone.id, ShippingRate.active.is_(True))
                       .order_by(ShippingRate.position, ShippingRate.amount_minor)).scalars()
    out = []
    for r in rates:
        if r.min_order_minor and merch_minor < r.min_order_minor:
            continue
        out.append({"id": str(r.id), "label": r.name if r.name else zone.name, "zone": zone.name,
                    "amount_minor": rate_amount(r, merch_minor, weight_grams, free_shipping), "eta_label": r.eta_label,
                    "cod_allowed": r.cod_allowed, "cod_fee_minor": r.cod_fee_minor if r.cod_allowed else 0})
    return out


def zone_dict(z: ShippingZone, rates: list[ShippingRate]) -> dict:
    return {"id": str(z.id), "name": z.name, "regions": z.regions, "position": z.position, "active": z.active,
            "is_seed_default": z.seed_key is not None, "rates": [rate_dict(r) for r in rates]}


def rate_dict(r: ShippingRate) -> dict:
    return {"id": str(r.id), "zone_id": str(r.zone_id), "name": r.name, "kind": r.kind,
            "amount_minor": r.amount_minor, "base_weight_grams": r.base_weight_grams, "per_kg_minor": r.per_kg_minor,
            "free_over_minor": r.free_over_minor, "min_order_minor": r.min_order_minor, "cod_allowed": r.cod_allowed,
            "cod_fee_minor": r.cod_fee_minor, "eta_label": r.eta_label, "courier_key": r.courier_key,
            "active": r.active, "position": r.position, "is_seed_default": r.is_seed_default}


# ---------------------------------------------------------------- courier accounts
def courier_ctx(db: Session, acc: CourierAccount) -> ProviderContext:
    return ProviderContext(db=db, store_id=acc.store_id, key=f"{acc.courier_key}.{acc.mode}", mode=acc.mode,
                           config=acc, secret_prefix="shipping")


def courier_state(acc: CourierAccount | None, key: str) -> str:
    entry = next((c for c in COURIER_CATALOG if c["key"] == key), None)
    base = entry["base_state"] if entry else "adapter_draft_unverified"
    if acc is not None and base == "adapter_draft_unverified":
        v = (acc.verification or {}).get("state")
        if v in ("sandbox_verified", "live_verified"):
            return v
    return base


def account_dict(db: Session, acc: CourierAccount) -> dict:
    from ..platform.models import SecretRecord

    prefix = f"shipping.{acc.courier_key}.{acc.mode}."
    hints = {r.ref[len(prefix):]: r.hint or "••••" for r in db.execute(
        select(SecretRecord).where(SecretRecord.store_id == acc.store_id, SecretRecord.ref.startswith(prefix))
    ).scalars()}
    adapter = COURIERS.get(acc.courier_key)
    return {"id": str(acc.id), "courier_key": acc.courier_key, "label": acc.label, "mode": acc.mode,
            "enabled": acc.enabled, "settings": acc.settings, "credentials": hints,
            "verification": acc.verification, "state": courier_state(acc, acc.courier_key),
            "simulation": bool(adapter and adapter.simulation),
            "webhook_url": f"{get_settings().public_url.rstrip('/')}/api/shipping/webhooks/{acc.courier_key}"}


# ---------------------------------------------------------------- shipments
def shipment_dict(s: Shipment, events: list[ShipmentEvent] | None = None, order: Order | None = None) -> dict:
    d = {"id": str(s.id), "order_id": str(s.order_id), "courier": s.courier_key,
         "courier_label": COURIERS[s.courier_key].label if s.courier_key in COURIERS else s.courier_key,
         "tracking_code": s.tracking_code, "tracking_url": s.tracking_url, "status": s.status,
         "status_label": SHIPMENT_LABELS.get(s.status, s.status), "lines": s.lines, "weight_grams": s.weight_grams,
         "cod_amount_minor": s.cod_amount_minor, "is_simulation": s.is_simulation, "restocked": s.restocked,
         "created_at": s.created_at.isoformat(), "delivered_at": s.delivered_at.isoformat() if s.delivered_at else None,
         "next_statuses": sorted(SHIPMENT_TRANSITIONS.get(s.status, set()))}
    if order is not None:
        d["order_number"] = order.number
        d["customer"] = order.name
        d["district"] = (order.shipping_address or {}).get("district")
    if events is not None:
        d["events"] = [{"status": e.status, "message": e.message, "at": e.at.isoformat(), "source": e.source,
                        "provider_status": e.provider_status} for e in events]
    return d


def shipment_events(db: Session, shipment_id: uuid.UUID) -> list[ShipmentEvent]:
    return list(db.execute(select(ShipmentEvent).where(ShipmentEvent.shipment_id == shipment_id)
                           .order_by(ShipmentEvent.at)).scalars())


def book_shipment(db: Session, store_id: uuid.UUID, order: Order, account_id: uuid.UUID,
                  lines: list[dict] | None, weight_grams: int | None, cod_amount_minor: int | None, note: str,
                  overrides: dict, actor: Actor) -> Shipment:
    """Book with a courier and convert reservations to shipped stock. Caller holds the order lock and commits."""
    from ..inventory.ledger import apply_movement, lock_levels

    acc = db.execute(select(CourierAccount).where(CourierAccount.id == account_id,
                                                  CourierAccount.store_id == store_id)).scalar_one_or_none()
    if acc is None:
        raise NotFound("Courier account")
    adapter = COURIERS.get(acc.courier_key)
    if adapter is None:
        raise Conflict("courier_unavailable", "No adapter is installed for this courier")
    if not acc.enabled:
        raise Conflict("courier_disabled", f"{acc.label} is not enabled")
    if not adapter.simulation and courier_state(acc, acc.courier_key) not in ("sandbox_verified", "live_verified"):
        raise Conflict("courier_not_verified", f"{adapter.label} credentials have not been verified; booking is "
                                               "disabled until Verify succeeds.")
    if order.status not in ("confirmed", "partially_shipped"):
        raise Conflict("not_shippable", f"Only confirmed orders can ship (this one is {order.status.replace('_', ' ')})")
    is_cod = order.payment_method == "cod"
    if not is_cod and order.payment_status not in ("paid", "partially_refunded"):
        raise Conflict("payment_required", "This order must be paid before it ships")
    all_lines = {str(ln.id): ln for ln in lines_of(db, order.id, lock=True)}
    wanted: list[tuple] = []
    if lines:
        for item in lines:
            ln = all_lines.get(str(item.get("line_id")))
            q = int(item.get("quantity") or 0)
            if ln is None or q <= 0:
                raise AppError("invalid_line", "A shipment line does not belong to this order")
            if q > ln.quantity - ln.fulfilled_qty:
                raise Conflict("over_fulfil", f"Only {ln.quantity - ln.fulfilled_qty} of {ln.title} remain to ship")
            wanted.append((ln, q))
    else:
        wanted = [(ln, ln.quantity - ln.fulfilled_qty) for ln in all_lines.values() if ln.quantity > ln.fulfilled_qty]
    if not wanted:
        raise Conflict("nothing_to_ship", "Every item on this order has already shipped")
    keys = [(ln.variant_id, ln.warehouse_id) for ln, _ in wanted if ln.warehouse_id and ln.variant_id]
    levels = lock_levels(db, keys) if keys else {}
    for ln, q in wanted:
        if ln.warehouse_id and ln.variant_id:
            from_res = min(q, ln.reserved_qty)
            apply_movement(db, store_id=store_id, variant_id=ln.variant_id, warehouse_id=ln.warehouse_id,
                           reason="ship", delta_on_hand=-q, delta_reserved=-from_res, ref_type="order",
                           ref_id=order.number, note="Shipped", actor_id=actor.id,
                           level=levels[(ln.variant_id, ln.warehouse_id)])
            ln.reserved_qty -= from_res
        ln.fulfilled_qty += q
    if cod_amount_minor is None:
        if is_cod:
            assigned = sum(s.cod_amount_minor for s in db.execute(select(Shipment).where(
                Shipment.order_id == order.id, Shipment.status.notin_(["cancelled", "returned"]))).scalars())
            cod_amount_minor = max(0, order.total_minor - order.paid_minor - assigned)
        else:
            cod_amount_minor = 0
    elif cod_amount_minor < 0 or (not is_cod and cod_amount_minor > 0):
        raise AppError("invalid_cod", "COD amount must be zero for prepaid orders")
    if weight_grams is None:
        weight_grams = max(500, sum(ln.weight_grams * q for ln, q in wanted))
    s = Shipment(id=uuid.uuid4(), store_id=store_id, order_id=order.id, account_id=acc.id,
                 courier_key=acc.courier_key, status="booked",
                 lines=[{"line_id": str(ln.id), "quantity": q, "title": ln.title, "sku": ln.sku} for ln, q in wanted],
                 weight_grams=weight_grams, cod_amount_minor=cod_amount_minor, is_simulation=adapter.simulation,
                 note=note or "", created_by=actor.id, provider_data=dict(overrides or {}))
    db.add(s)
    db.flush()
    res = adapter.book(courier_ctx(db, acc), s, order, [(ln, q) for ln, q in wanted])
    s.tracking_code, s.tracking_url = res["tracking_code"], res.get("tracking_url")
    s.provider_data = {**(s.provider_data or {}), "booking": res.get("raw", {})}
    db.add(ShipmentEvent(shipment_id=s.id, courier_key=s.courier_key, status="booked", source="staff",
                         message=f"Booked with {adapter.label}" + (" (simulation)" if adapter.simulation else "")))
    timeline(db, order, "shipment", f"Shipment {s.tracking_code} booked with {adapter.label}", public=True,
             actor=actor, data={"shipment_id": str(s.id)})
    audit(db, store_id, actor, "shipping.shipment_booked", "order", order.id,
          {"shipment_id": str(s.id), "courier": acc.courier_key, "cod_amount_minor": cod_amount_minor})
    recompute_fulfillment(db, order, actor)
    return s


def apply_courier_event(db: Session, shipment: Shipment, ev: CourierEvent, source: str,
                        actor: Actor = SYSTEM) -> str:
    """Apply one tracking event (dedupe by provider event id). Caller commits."""
    from ..inventory.ledger import apply_movement
    from ..payments.service import cod_intent, record_cod_collected

    if ev.event_id and db.execute(select(ShipmentEvent.id).where(ShipmentEvent.courier_key == shipment.courier_key,
                                                                 ShipmentEvent.provider_event_id == ev.event_id)
                                  ).first():
        return "duplicate"
    order = db.execute(select(Order).where(Order.id == shipment.order_id).with_for_update()).scalar_one()
    frm = shipment.status
    valid = ev.status == frm or ev.status in SHIPMENT_TRANSITIONS.get(frm, set())
    db.add(ShipmentEvent(shipment_id=shipment.id, courier_key=shipment.courier_key, provider_event_id=ev.event_id,
                         status=ev.status, provider_status=ev.provider_status, source=source,
                         message=ev.message if valid else f"{ev.message} (ignored: cannot move from {frm})",
                         raw=ev.raw, at=ev.at or utcnow()))
    if not valid or ev.status == frm:
        return "ignored" if not valid else "recorded"
    shipment.status = ev.status
    label = SHIPMENT_LABELS.get(ev.status, ev.status)
    timeline(db, order, "shipment", f"Shipment {shipment.tracking_code}: {label}", public=True, actor=actor,
             data={"shipment_id": str(shipment.id), "status": ev.status})
    if ev.status == "delivered":
        shipment.delivered_at = utcnow()
        if shipment.cod_amount_minor > 0:
            record_cod_collected(db, order, shipment.cod_amount_minor, COURIERS[shipment.courier_key].label
                                 if shipment.courier_key in COURIERS else shipment.courier_key)
    elif ev.status == "failed_attempt":
        from ..platform.api import notify

        notify(db, order.store_id, "shipment.failed_attempt", f"Delivery failed for {order.number}",
               ev.message or "The courier could not deliver", f"/admin/orders/{order.number}", "shipping.read",
               "warning")
        emit(db, order.store_id, EV_SHIPMENT_FAILED, {"order_id": str(order.id), "shipment_id": str(shipment.id)})
    elif ev.status == "returned" and not shipment.restocked:
        by_id = {str(ln.id): ln for ln in lines_of(db, order.id, lock=True)}
        restocked = 0
        for item in shipment.lines:
            ln = by_id.get(item["line_id"])
            if ln and ln.variant_id and ln.warehouse_id:
                q = min(item["quantity"], ln.fulfilled_qty - ln.returned_qty)
                if q > 0:
                    apply_movement(db, store_id=order.store_id, variant_id=ln.variant_id,
                                   warehouse_id=ln.warehouse_id, reason="return", delta_on_hand=q,
                                   ref_type="shipment", ref_id=str(shipment.id), note=f"RTO {shipment.tracking_code}",
                                   actor_id=actor.id)
                    ln.returned_qty += q
                    restocked += q
        shipment.restocked = True
        timeline(db, order, "stock", f"Restocked {restocked} unit(s) from returned shipment {shipment.tracking_code}",
                 actor=actor)
        it = cod_intent(db, order.id)
        if it is not None and it.captured_minor == 0:
            others = db.execute(select(Shipment.id).where(Shipment.order_id == order.id, Shipment.id != shipment.id,
                                                          Shipment.status.notin_(["returned", "cancelled"]))).first()
            if others is None:
                it.status = "cancelled"
                it.failure_reason = "Returned to sender; cash was not collected"
                if order.paid_minor == 0:
                    order.payment_status = "voided"
    emit(db, order.store_id, EV_SHIPMENT_STATUS, {"order_id": str(order.id), "number": order.number,
                                                  "shipment_id": str(shipment.id), "from": frm, "to": ev.status})
    recompute_fulfillment(db, order, actor)
    return "applied"


def cancel_shipment(db: Session, shipment: Shipment, actor: Actor) -> None:
    from ..inventory.ledger import apply_movement

    if shipment.status != "booked":
        raise Conflict("cannot_cancel", "Only shipments that have not been picked up can be cancelled")
    acc = db.get(CourierAccount, shipment.account_id) if shipment.account_id else None
    adapter = COURIERS.get(shipment.courier_key)
    if adapter is None or acc is None:
        raise Conflict("courier_unavailable", "Courier adapter not available")
    adapter.cancel(courier_ctx(db, acc), shipment)
    order = db.execute(select(Order).where(Order.id == shipment.order_id).with_for_update()).scalar_one()
    by_id = {str(ln.id): ln for ln in lines_of(db, order.id, lock=True)}
    for item in shipment.lines:
        ln = by_id.get(item["line_id"])
        if ln is None:
            continue
        q = item["quantity"]
        if ln.variant_id and ln.warehouse_id:
            # Goods never left: put them back on hand and re-reserve for this order.
            apply_movement(db, store_id=order.store_id, variant_id=ln.variant_id, warehouse_id=ln.warehouse_id,
                           reason="return", delta_on_hand=q, ref_type="shipment", ref_id=str(shipment.id),
                           note="Shipment cancelled before pickup", actor_id=actor.id)
            apply_movement(db, store_id=order.store_id, variant_id=ln.variant_id, warehouse_id=ln.warehouse_id,
                           reason="reserve", delta_reserved=q, ref_type="order", ref_id=order.number,
                           note="Re-reserved after shipment cancel", actor_id=actor.id, allow_negative_available=True)
            ln.reserved_qty += q
        ln.fulfilled_qty -= q
    shipment.status = "cancelled"
    db.add(ShipmentEvent(shipment_id=shipment.id, courier_key=shipment.courier_key, status="cancelled", source="staff",
                         message="Cancelled by staff"))
    timeline(db, order, "shipment", f"Shipment {shipment.tracking_code} cancelled", actor=actor)
    audit(db, order.store_id, actor, "shipping.shipment_cancelled", "order", order.id,
          {"shipment_id": str(shipment.id)})
    db.flush()
    recompute_fulfillment(db, order, actor)


# ---------------------------------------------------------------- webhooks & simulation
def handle_courier_webhook(db: Session, store, courier_key: str, headers: dict[str, str], raw: bytes,
                           query: dict[str, str]) -> dict:
    adapter = COURIERS.get(courier_key)
    if adapter is None:
        raise NotFound("Courier")
    accounts = list(db.execute(select(CourierAccount).where(CourierAccount.store_id == store.id,
                                                            CourierAccount.courier_key == courier_key)).scalars())
    if not accounts:
        raise NotFound("Courier account")
    events, last_err = None, None
    for acc in accounts:
        try:
            events = adapter.parse_webhook(courier_ctx(db, acc), headers, raw, query)
            break
        except InvalidSignature as exc:
            last_err = exc
    if events is None:
        log.warning("Rejected %s courier webhook: %s", courier_key, last_err)
        return {"status": "rejected", "reason": str(last_err), "http": 400}
    results = []
    for ev in events:
        s = db.execute(select(Shipment).where(Shipment.store_id == store.id, Shipment.courier_key == courier_key,
                                              Shipment.tracking_code == ev.tracking_code).with_for_update()
                       ).scalar_one_or_none()
        if s is None:
            results.append("unknown_shipment")
            continue
        results.append(apply_courier_event(db, s, ev, "webhook"))
    db.commit()
    return {"status": "ok", "results": results, "http": 200}


def simulate_courier_event(db: Session, store, shipment: Shipment, status: str, message: str | None,
                           actor: Actor) -> dict:
    """Sandbox courier only: sign a status event and push it through the real webhook path."""
    if not shipment.is_simulation or shipment.courier_key != "sandbox":
        raise Conflict("not_simulation", "Only sandbox-courier shipments can be advanced manually")
    if status not in SHIPMENT_TRANSITIONS.get(shipment.status, set()):
        raise Conflict("invalid_transition", f"A shipment that is {shipment.status.replace('_', ' ')} cannot move to "
                                             f"{status.replace('_', ' ')}",
                       details={"allowed": sorted(SHIPMENT_TRANSITIONS.get(shipment.status, set()))})
    body = json.dumps({"id": f"sevt_{uuid.uuid4().hex}", "tracking_code": shipment.tracking_code, "status": status,
                       "message": message or f"[Simulation] {SHIPMENT_LABELS.get(status, status)}",
                       "created": int(time.time())})
    secret = ensure_secret(db, store.id, SANDBOX_COURIER_SECRET_REF, "shipping.sandbox")
    headers = {SIGNATURE_HEADER: sign(secret, body)}
    audit(db, store.id, actor, "shipping.simulated_event", "shipment", shipment.id, {"status": status})
    db.commit()
    return handle_courier_webhook(db, store, "sandbox", headers, body.encode(), {})


def poll_tracking(db: Session, shipment: Shipment, actor: Actor) -> dict:
    acc = db.get(CourierAccount, shipment.account_id) if shipment.account_id else None
    adapter = COURIERS.get(shipment.courier_key)
    if adapter is None or acc is None or "track" not in adapter.capabilities:
        raise Conflict("tracking_unsupported", "This courier does not support tracking lookups")
    events = adapter.track(courier_ctx(db, acc), shipment)
    out = [apply_courier_event(db, shipment, ev, "poll", actor) for ev in events]
    db.commit()
    return {"results": out}


def cod_reconciliation(db: Session, store_id: uuid.UUID) -> dict:
    from ..payments.models import PaymentIntent

    rows = db.execute(select(Shipment, Order).join(Order, Order.id == Shipment.order_id)
                      .where(Shipment.store_id == store_id, Shipment.cod_amount_minor > 0)
                      .order_by(Shipment.created_at.desc())).all()
    intents = {it.order_id: it for it in db.execute(select(PaymentIntent).where(
        PaymentIntent.store_id == store_id, PaymentIntent.provider == "cod")).scalars()}
    per: dict[str, dict] = {}
    items = []
    for s, o in rows:
        c = per.setdefault(s.courier_key, {"courier": s.courier_key, "expected_minor": 0, "collected_minor": 0,
                                           "remitted_minor": 0, "due_minor": 0, "shipments": 0})
        it = intents.get(o.id)
        c["shipments"] += 1
        if s.status not in ("cancelled", "returned"):
            c["expected_minor"] += s.cod_amount_minor
        collected = s.status == "delivered"
        remitted = bool(collected and it and it.remitted_at)
        if collected:
            c["collected_minor"] += s.cod_amount_minor
            if remitted:
                c["remitted_minor"] += s.cod_amount_minor
            else:
                c["due_minor"] += s.cod_amount_minor
        items.append({"shipment_id": str(s.id), "order_number": o.number, "courier": s.courier_key,
                      "tracking_code": s.tracking_code, "status": s.status, "cod_amount_minor": s.cod_amount_minor,
                      "collected": collected, "remitted": remitted, "intent_id": str(it.id) if it else None,
                      "remittance_ref": it.remittance_ref if it else None, "is_simulation": s.is_simulation})
    return {"couriers": list(per.values()), "items": items}
