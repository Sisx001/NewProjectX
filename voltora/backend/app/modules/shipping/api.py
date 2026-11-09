"""Shipping APIs: zones & rates, courier accounts, shipments, courier webhooks, printable documents, COD recon."""
from __future__ import annotations

import html
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.deps import Principal, current_store, parse_uuid, require
from ...core.errors import AppError, Conflict, NotFound
from ...core.security import mask, seal
from ...core.services import audit, get_setting, limiter
from ...db import get_db, utcnow
from ..commerce.models import Order
from ..commerce.orders import STATUS_LABELS, get_order, lines_of
from ..payments.providers.base import ProviderError, ProviderUnavailable
from . import locations
from . import service as svc
from .couriers import COURIERS
from .couriers.drafts import CATALOG as COURIER_CATALOG
from .models import CourierAccount, Shipment, ShippingRate, ShippingZone

router = APIRouter(prefix="/api", tags=["shipping"])
MODULE = {"title": "Shipping", "description": "Zones & rates, courier adapters, shipments, tracking, COD reconciliation"}


# ================================================================ zones & rates
class Region(BaseModel):
    division: str | None = Field(default=None, max_length=120)
    district: str | None = Field(default=None, max_length=120)
    upazila: str | None = Field(default=None, max_length=120)


class ZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    regions: list[Region] = Field(default_factory=list, max_length=1000)
    position: int = Field(default=0, ge=0, le=10000)
    active: bool = True


class RateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="flat", pattern="^(flat|weight)$")
    amount_minor: int = Field(ge=0, le=10_000_000)
    base_weight_grams: int = Field(default=1000, ge=0, le=1_000_000)
    per_kg_minor: int = Field(default=0, ge=0, le=10_000_000)
    free_over_minor: int | None = Field(default=None, ge=0)
    min_order_minor: int = Field(default=0, ge=0)
    cod_allowed: bool = True
    cod_fee_minor: int = Field(default=0, ge=0, le=10_000_000)
    eta_label: str = Field(default="", max_length=80)
    courier_key: str | None = Field(default=None, max_length=40)
    active: bool = True
    position: int = Field(default=0, ge=0, le=10000)


def _clean_regions(regions: list[Region]) -> list[dict]:
    out = []
    for r in regions:
        d = {k: v.strip() for k, v in r.model_dump().items() if v and v.strip()}
        if not d:
            continue
        if d.get("district"):
            loc = locations.resolve(d["district"], d.get("upazila"))
            if loc is None:
                raise AppError("invalid_region", f"Unknown district {d['district']}")
            if d.get("upazila") and loc["upazila"] is None:
                raise AppError("invalid_region", f"{d['upazila']} is not in {loc['district']}")
            d["district"] = loc["district"]
            if loc["upazila"]:
                d["upazila"] = loc["upazila"]
        elif d.get("upazila"):
            raise AppError("invalid_region", "An upazila needs its district")
        elif d.get("division") and not locations.districts_of(d["division"]):
            raise AppError("invalid_region", f"Unknown division {d['division']}")
        out.append(d)
    return out


def _zones(db: Session, store_id) -> list[dict]:
    zones = list(db.execute(select(ShippingZone).where(ShippingZone.store_id == store_id)
                            .order_by(ShippingZone.position, ShippingZone.created_at)).scalars())
    rates: dict = {}
    for r in db.execute(select(ShippingRate).where(ShippingRate.zone_id.in_([z.id for z in zones]))
                        .order_by(ShippingRate.position, ShippingRate.created_at)).scalars() if zones else []:
        rates.setdefault(r.zone_id, []).append(r)
    return [svc.zone_dict(z, rates.get(z.id, [])) for z in zones]


@router.get("/admin/shipping/zones")
def list_zones(db: Session = Depends(get_db), p: Principal = Depends(require("shipping.read"))):
    return {"items": _zones(db, p.store.id)}


@router.post("/admin/shipping/zones")
def create_zone(body: ZoneIn, db: Session = Depends(get_db), p: Principal = Depends(require("shipping.manage"))):
    z = ShippingZone(store_id=p.store.id, name=body.name.strip(), regions=_clean_regions(body.regions),
                     position=body.position, active=body.active)
    db.add(z)
    db.flush()
    audit(db, p.store.id, p.actor, "shipping.zone_created", "shipping_zone", z.id, {"name": z.name})
    db.commit()
    return svc.zone_dict(z, [])


def _zone(db: Session, p: Principal, zone_id: str) -> ShippingZone:
    z = db.get(ShippingZone, parse_uuid(zone_id, "Zone"))
    if z is None or z.store_id != p.store.id:
        raise NotFound("Zone")
    return z


@router.put("/admin/shipping/zones/{zone_id}")
def update_zone(zone_id: str, body: ZoneIn, db: Session = Depends(get_db),
                p: Principal = Depends(require("shipping.manage"))):
    z = _zone(db, p, zone_id)
    z.name, z.regions, z.position, z.active = body.name.strip(), _clean_regions(body.regions), body.position, \
        body.active
    audit(db, p.store.id, p.actor, "shipping.zone_updated", "shipping_zone", z.id, {"name": z.name})
    db.commit()
    return next(x for x in _zones(db, p.store.id) if x["id"] == str(z.id))


@router.delete("/admin/shipping/zones/{zone_id}")
def delete_zone(zone_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("shipping.manage"))):
    z = _zone(db, p, zone_id)
    audit(db, p.store.id, p.actor, "shipping.zone_deleted", "shipping_zone", z.id, {"name": z.name})
    db.delete(z)
    db.commit()
    return {"ok": True}


@router.post("/admin/shipping/zones/{zone_id}/rates")
def create_rate(zone_id: str, body: RateIn, db: Session = Depends(get_db),
                p: Principal = Depends(require("shipping.manage"))):
    z = _zone(db, p, zone_id)
    r = ShippingRate(zone_id=z.id, **body.model_dump())
    db.add(r)
    db.flush()
    audit(db, p.store.id, p.actor, "shipping.rate_created", "shipping_rate", r.id, body.model_dump())
    db.commit()
    return svc.rate_dict(r)


def _rate(db: Session, p: Principal, rate_id: str) -> ShippingRate:
    r = db.get(ShippingRate, parse_uuid(rate_id, "Rate"))
    if r is None or db.get(ShippingZone, r.zone_id).store_id != p.store.id:
        raise NotFound("Rate")
    return r


@router.put("/admin/shipping/rates/{rate_id}")
def update_rate(rate_id: str, body: RateIn, db: Session = Depends(get_db),
                p: Principal = Depends(require("shipping.manage"))):
    r = _rate(db, p, rate_id)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    r.is_seed_default = False
    audit(db, p.store.id, p.actor, "shipping.rate_updated", "shipping_rate", r.id, body.model_dump())
    db.commit()
    return svc.rate_dict(r)


@router.delete("/admin/shipping/rates/{rate_id}")
def delete_rate(rate_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("shipping.manage"))):
    r = _rate(db, p, rate_id)
    audit(db, p.store.id, p.actor, "shipping.rate_deleted", "shipping_rate", r.id, {"name": r.name})
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.get("/admin/shipping/estimate")
def estimate(district: str, upazila: str | None = None, subtotal_minor: int = Query(0, ge=0),
             weight_grams: int = Query(1000, ge=0), db: Session = Depends(get_db),
             p: Principal = Depends(require("shipping.read"))):
    """Shipping estimate simulator: what a customer at this address would be offered."""
    loc = locations.resolve(district, upazila)
    if loc is None:
        raise AppError("invalid_address", "Unknown district")
    zone = svc.match_zone(db, p.store.id, {"district": district, "upazila": upazila})
    return {"resolved": loc, "zone": zone.name if zone else None,
            "options": svc.shipping_options(db, p.store.id, {"district": district, "upazila": upazila},
                                            subtotal_minor, weight_grams)}


# ================================================================ couriers
@router.get("/admin/shipping/couriers")
def couriers(db: Session = Depends(get_db), p: Principal = Depends(require("shipping.read"))):
    accounts = list(db.execute(select(CourierAccount).where(CourierAccount.store_id == p.store.id)
                               .order_by(CourierAccount.created_at)).scalars())
    out = []
    for entry in COURIER_CATALOG:
        a = COURIERS.get(entry["key"])
        out.append({**entry, "has_adapter": a is not None,
                    "capabilities": sorted(a.capabilities) if a else [],
                    "credential_fields": a.credential_fields if a else [],
                    "settings_fields": a.settings_fields if a else [], "docs_url": a.docs_url if a else None,
                    "status_map": a.status_map if a and not a.simulation else {},
                    "accounts": [svc.account_dict(db, x) for x in accounts if x.courier_key == entry["key"]]})
    return {"items": out}


class AccountIn(BaseModel):
    courier_key: str = Field(max_length=40)
    label: str = Field(min_length=1, max_length=120)
    mode: str = Field(default="sandbox", pattern="^(sandbox|live)$")
    settings: dict[str, str] = Field(default_factory=dict)


@router.post("/admin/shipping/couriers/accounts")
def create_account(body: AccountIn, db: Session = Depends(get_db),
                   p: Principal = Depends(require("shipping.manage"))):
    a = COURIERS.get(body.courier_key)
    if a is None:
        raise AppError("no_adapter", "VOLTORA has no adapter for this courier yet (catalog entry only)", status=409)
    if db.execute(select(CourierAccount.id).where(CourierAccount.store_id == p.store.id,
                                                  CourierAccount.courier_key == body.courier_key,
                                                  CourierAccount.mode == body.mode)).first():
        raise Conflict("exists", "An account for this courier and mode already exists")
    allowed = {f["key"] for f in a.settings_fields}
    acc = CourierAccount(store_id=p.store.id, courier_key=body.courier_key, label=body.label.strip(), mode=body.mode,
                         enabled=False, settings={k: v for k, v in body.settings.items() if k in allowed},
                         verification={"state": "simulation" if a.simulation else "unverified"})
    db.add(acc)
    db.flush()
    audit(db, p.store.id, p.actor, "shipping.courier_account_created", "courier_account", acc.id,
          {"courier": body.courier_key, "mode": body.mode})
    db.commit()
    return svc.account_dict(db, acc)


def _account(db: Session, p: Principal, account_id: str) -> CourierAccount:
    acc = db.get(CourierAccount, parse_uuid(account_id, "Courier account"))
    if acc is None or acc.store_id != p.store.id:
        raise NotFound("Courier account")
    return acc


class AccountPatch(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None
    settings: dict[str, str] | None = None


@router.patch("/admin/shipping/couriers/accounts/{account_id}")
def patch_account(account_id: str, body: AccountPatch, db: Session = Depends(get_db),
                  p: Principal = Depends(require("shipping.manage"))):
    acc = _account(db, p, account_id)
    a = COURIERS.get(acc.courier_key)
    if body.enabled:
        st = svc.courier_state(acc, acc.courier_key)
        if st not in ("simulation", "sandbox_verified", "live_verified"):
            raise AppError("cannot_enable", "Verify this courier's credentials before enabling it", status=409)
    if body.label is not None:
        acc.label = body.label.strip()
    if body.enabled is not None:
        acc.enabled = body.enabled
    if body.settings is not None and a:
        allowed = {f["key"] for f in a.settings_fields}
        acc.settings = {**(acc.settings or {}), **{k: v.strip() for k, v in body.settings.items() if k in allowed}}
    audit(db, p.store.id, p.actor, "shipping.courier_account_updated", "courier_account", acc.id,
          body.model_dump(exclude_none=True))
    db.commit()
    return svc.account_dict(db, acc)


class CredIn(BaseModel):
    values: dict[str, str]


@router.put("/admin/shipping/couriers/accounts/{account_id}/credentials")
def account_credentials(account_id: str, body: CredIn, db: Session = Depends(get_db),
                        p: Principal = Depends(require("shipping.manage"))):
    from ..platform.models import SecretRecord

    acc = _account(db, p, account_id)
    a = COURIERS.get(acc.courier_key)
    allowed = {f["key"] for f in (a.credential_fields if a else [])}
    unknown = set(body.values) - allowed
    if unknown or not allowed:
        raise AppError("unknown_fields", f"Unknown credential fields: {', '.join(sorted(unknown)) or 'none allowed'}")
    for name, value in body.values.items():
        if not value:
            continue
        ref = f"shipping.{acc.courier_key}.{acc.mode}.{name}"
        rec = db.execute(select(SecretRecord).where(SecretRecord.store_id == p.store.id, SecretRecord.ref == ref)
                         ).scalar_one_or_none()
        if rec is None:
            db.add(SecretRecord(store_id=p.store.id, ref=ref, sealed=seal(value), hint=mask(value),
                                owner_kind="core", owner_ref=f"shipping.{acc.courier_key}"))
        else:
            rec.sealed, rec.hint, rec.rotated_at = seal(value), mask(value), utcnow()
    acc.verification = {"state": "unverified", "message": "Credentials changed; verify again.",
                        "checked_at": utcnow().isoformat()}
    acc.enabled = False
    audit(db, p.store.id, p.actor, "shipping.courier_credentials_saved", "courier_account", acc.id,
          {"fields": sorted(body.values)})
    db.commit()
    return svc.account_dict(db, acc)


@router.post("/admin/shipping/couriers/accounts/{account_id}/verify")
def verify_account(account_id: str, db: Session = Depends(get_db),
                   p: Principal = Depends(require("shipping.manage"))):
    limiter.hit(f"courier-verify:{account_id}", 10, 600)
    acc = _account(db, p, account_id)
    a = COURIERS.get(acc.courier_key)
    now = utcnow().isoformat()
    if a is None:
        res = {"ok": False, "state": "catalog_only", "message": "Not implemented: no adapter for this courier."}
    elif a.simulation:
        ok, msg = a.verify_credentials(svc.courier_ctx(db, acc))
        res = {"ok": ok, "state": "simulation", "message": msg}
    else:
        missing = [f["key"] for f in a.credential_fields if not f["key"].startswith("webhook")
                   and f["key"] not in svc.account_dict(db, acc)["credentials"]]
        if missing:
            res = {"ok": False, "state": "adapter_draft_unverified",
                   "message": f"Save these credentials first: {', '.join(missing)}"}
        else:
            try:
                ok, msg = a.verify_credentials(svc.courier_ctx(db, acc))
            except (ProviderUnavailable, ProviderError) as exc:
                ok, msg = False, exc.message
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, f"Verification failed: {exc}"[:300]
            state = f"{acc.mode}_verified" if ok else "unverified"
            acc.verification = {"state": state, "message": msg, "checked_at": now}
            res = {"ok": ok, "state": state if ok else "adapter_draft_unverified", "message": msg}
    audit(db, p.store.id, p.actor, "shipping.courier_verify", "courier_account", acc.id,
          {"ok": res["ok"], "state": res["state"]})
    db.commit()
    return {**res, "checked_at": now}


# ================================================================ shipments
@router.get("/admin/shipments")
def list_shipments(status: str | None = None, courier: str | None = None, q: str | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                   db: Session = Depends(get_db), p: Principal = Depends(require("shipping.read"))):
    conds = [Shipment.store_id == p.store.id]
    if status:
        conds.append(Shipment.status.in_(status.split(",")))
    if courier:
        conds.append(Shipment.courier_key == courier)
    if q:
        conds.append((Shipment.tracking_code.ilike(f"%{q}%")) | (Order.number.ilike(f"%{q}%")))
    total = db.execute(select(func.count()).select_from(Shipment).join(Order, Order.id == Shipment.order_id)
                       .where(*conds)).scalar_one()
    rows = db.execute(select(Shipment, Order).join(Order, Order.id == Shipment.order_id).where(*conds)
                      .order_by(Shipment.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    counts = dict(db.execute(select(Shipment.status, func.count()).where(Shipment.store_id == p.store.id)
                             .group_by(Shipment.status)).all())
    ready = db.execute(select(func.count()).select_from(Order).where(
        Order.store_id == p.store.id, Order.status == "confirmed", Order.fulfillment_status == "unfulfilled")
    ).scalar_one()
    return {"items": [svc.shipment_dict(s, order=o) for s, o in rows], "total": total, "page": page,
            "page_size": page_size, "status_counts": counts, "orders_ready_to_ship": ready}


class BookIn(BaseModel):
    account_id: uuid.UUID
    lines: list[dict] | None = Field(default=None, max_length=200)
    weight_grams: int | None = Field(default=None, ge=1, le=100_000)
    cod_amount_minor: int | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=500)
    overrides: dict[str, str] = Field(default_factory=dict)


@router.post("/admin/orders/{number}/shipments")
def book(number: str, body: BookIn, db: Session = Depends(get_db),
         p: Principal = Depends(require("shipping.manage"))):
    from ..commerce.api import admin_order_detail

    o = get_order(db, p.store.id, number, lock=True)
    svc.book_shipment(db, p.store.id, o, body.account_id, body.lines, body.weight_grams, body.cod_amount_minor,
                      body.note, {k: v for k, v in body.overrides.items() if k in ("city_id", "zone_id", "area_id")},
                      p.actor)
    db.commit()
    return admin_order_detail(db, p, o)


def _shipment(db: Session, p: Principal, shipment_id: str, lock: bool = False) -> Shipment:
    q = select(Shipment).where(Shipment.id == parse_uuid(shipment_id, "Shipment"), Shipment.store_id == p.store.id)
    s = db.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if s is None:
        raise NotFound("Shipment")
    return s


class SimulateIn(BaseModel):
    status: str
    message: str | None = Field(default=None, max_length=300)


@router.post("/admin/shipments/{shipment_id}/simulate")
def simulate(shipment_id: str, body: SimulateIn, db: Session = Depends(get_db),
             p: Principal = Depends(require("shipping.manage"))):
    s = _shipment(db, p, shipment_id)
    res = svc.simulate_courier_event(db, p.store, s, body.status, body.message, p.actor)
    db.expire_all()
    s = _shipment(db, p, shipment_id)
    return {"result": res, "shipment": svc.shipment_dict(s, svc.shipment_events(db, s.id))}


@router.post("/admin/shipments/{shipment_id}/cancel")
def cancel(shipment_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("shipping.manage"))):
    s = _shipment(db, p, shipment_id, lock=True)
    svc.cancel_shipment(db, s, p.actor)
    db.commit()
    return svc.shipment_dict(s, svc.shipment_events(db, s.id))


@router.post("/admin/shipments/{shipment_id}/track")
def track_now(shipment_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("shipping.manage"))):
    s = _shipment(db, p, shipment_id, lock=True)
    res = svc.poll_tracking(db, s, p.actor)
    return {**res, "shipment": svc.shipment_dict(s, svc.shipment_events(db, s.id))}


@router.get("/admin/shipping/cod")
def cod(db: Session = Depends(get_db), p: Principal = Depends(require("payments.read"))):
    return svc.cod_reconciliation(db, p.store.id)


@router.post("/shipping/webhooks/{courier_key}")
async def courier_webhook(courier_key: str, request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > 256 * 1024:
        return JSONResponse({"error": {"code": "too_large", "message": "Payload too large"}}, status_code=413)
    store = current_store(db)
    res = svc.handle_courier_webhook(db, store, courier_key, {k.lower(): v for k, v in request.headers.items()},
                                     raw, dict(request.query_params))
    code = res.pop("http")
    return JSONResponse(res, status_code=code)


# ================================================================ printable documents
DOC_CSS = """
body{font:14px/1.45 'Instrument Sans',system-ui,sans-serif;color:#1b1a17;margin:32px;max-width:800px}
h1{font-size:22px;margin:0}table{width:100%;border-collapse:collapse;margin-top:16px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd8cc}th{font-size:12px;color:#6b665c}
.r{text-align:right}.muted{color:#6b665c}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:20px}
.mono{font-family:'JetBrains Mono',monospace}.box{border:1px solid #1b1a17;padding:4px 8px;display:inline-block}
.tot td{border:none;padding:2px 8px}@media print{body{margin:0}}
"""


def _doc(title: str, body: str) -> HTMLResponse:
    page = f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>" \
           f"<style>{DOC_CSS}</style></head><body>{body}</body></html>"
    return HTMLResponse(page, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                                                  "img-src https: data:", "Cache-Control": "no-store"})


def _addr_html(a: dict) -> str:
    parts = [a.get("name"), a.get("phone"), a.get("line1"), a.get("line2"), a.get("area"),
             ", ".join(x for x in (a.get("upazila"), a.get("district")) if x), a.get("postal_code")]
    return "<br>".join(html.escape(str(x)) for x in parts if x)


def _money(minor: int) -> str:
    return f"৳{minor / 100:,.2f}"


@router.get("/admin/orders/{number}/packing-slip")
def packing_slip(number: str, db: Session = Depends(get_db), p: Principal = Depends(require("orders.read"))):
    o = get_order(db, p.store.id, number)
    rows = "".join(f"<tr><td class='mono'>{html.escape(ln.sku)}</td><td>{html.escape(ln.title)}"
                   f"{' — ' + html.escape(ln.variant_title) if ln.variant_title else ''}</td>"
                   f"<td class='r'>{ln.quantity}</td><td class='r'>{ln.fulfilled_qty}</td></tr>"
                   for ln in lines_of(db, o.id))
    ships = db.execute(select(Shipment).where(Shipment.order_id == o.id, Shipment.status != "cancelled")).scalars()
    ship_html = "".join(f"<div>{html.escape(s.courier_key)} · <span class='mono'>{html.escape(s.tracking_code or '')}"
                        f"</span> · COD {_money(s.cod_amount_minor)}{' (simulation)' if s.is_simulation else ''}</div>"
                        for s in ships)
    body = (f"<h1>Packing slip</h1><div class='muted'>{html.escape(p.store.name)}</div>"
            f"<div class='grid'><div><strong>Order</strong><br><span class='box mono'>{html.escape(o.number)}</span>"
            f"<br>{o.placed_at:%d %b %Y}<br>Payment: {html.escape(o.payment_method)} · "
            f"{html.escape(o.payment_status)}</div><div><strong>Ship to</strong><br>{_addr_html(o.shipping_address)}"
            f"</div></div><table><thead><tr><th>SKU</th><th>Item</th><th class='r'>Qty</th><th class='r'>Shipped</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
            f"{'<p><strong>Shipments</strong></p>' + ship_html if ship_html else ''}"
            f"{'<p><strong>Customer note</strong><br>' + html.escape(o.note) + '</p>' if o.note else ''}")
    return _doc(f"Packing slip {o.number}", body)


@router.get("/admin/orders/{number}/invoice")
def invoice(number: str, db: Session = Depends(get_db), p: Principal = Depends(require("orders.read"))):
    o = get_order(db, p.store.id, number)
    rows = "".join(f"<tr><td>{html.escape(ln.title)}{' — ' + html.escape(ln.variant_title) if ln.variant_title else ''}"
                   f"<div class='muted mono'>{html.escape(ln.sku)}</div></td><td class='r'>{ln.quantity}</td>"
                   f"<td class='r'>{_money(ln.unit_price_minor)}</td><td class='r'>{_money(ln.total_minor)}</td></tr>"
                   for ln in lines_of(db, o.id))
    tot = [("Subtotal", o.subtotal_minor)]
    if o.discount_minor:
        tot.append((f"Discount{' (' + html.escape(o.discount_code) + ')' if o.discount_code else ''}",
                    -o.discount_minor))
    tot.append((f"Delivery — {html.escape(o.shipping_label)}", o.shipping_minor))
    if o.payment_fee_minor:
        tot.append(("Payment fee", o.payment_fee_minor))
    if o.tax_minor:
        tot.append(("VAT (included)" if o.tax_inclusive else "VAT", o.tax_minor))
    tot.append(("<strong>Total</strong>", o.total_minor))
    if o.paid_minor:
        tot.append(("Paid", o.paid_minor))
    if o.refunded_minor:
        tot.append(("Refunded", -o.refunded_minor))
    tot_html = "".join(f"<tr><td class='r'>{label}</td><td class='r' style='width:140px'>{_money(v)}</td></tr>"
                       for label, v in tot)
    addr = get_setting(db, p.store.id, "store.address") or ""
    body = (f"<h1>Invoice {html.escape(o.number)}</h1><div class='muted'>{html.escape(p.store.name)}"
            f"{'<br>' + html.escape(addr) if addr else ''}</div>"
            f"{'<p class=box>TEST ORDER — sandbox payment, not a real sale</p>' if o.is_test else ''}"
            f"<div class='grid'><div><strong>Bill to</strong><br>{_addr_html(o.shipping_address)}</div>"
            f"<div><strong>Date</strong> {o.placed_at:%d %b %Y}<br><strong>Status</strong> "
            f"{html.escape(STATUS_LABELS.get(o.status, o.status))}<br><strong>Payment</strong> "
            f"{html.escape(o.payment_method)} · {html.escape(o.payment_status)}</div></div>"
            f"<table><thead><tr><th>Item</th><th class='r'>Qty</th><th class='r'>Unit</th><th class='r'>Amount</th>"
            f"</tr></thead><tbody>{rows}</tbody></table><table class='tot'>{tot_html}</table>")
    return _doc(f"Invoice {o.number}", body)
