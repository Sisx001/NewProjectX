"""Pathao, Steadfast and RedX — ADAPTER DRAFTS, UNVERIFIED.

Written from the providers' public merchant API documentation and community SDKs; request shapes are pinned by respx
contract tests but no call has ever reached the providers from this environment. Fixes vs legacy:
* every call uses the host for the account's mode (legacy Pathao tracked on LIVE, RedX on STAGE always);
* COD amounts are converted from minor units to whole/decimal taka (legacy Pathao sent poisha → 100× overcharge);
* weights are converted from grams explicitly (legacy Pathao sent 1000 "kg");
* HTTP errors are surfaced instead of being reported as success.
"""
from __future__ import annotations

import hmac

import httpx

from ...payments.providers.base import InvalidSignature, ProviderError, require_external_network
from .base import CourierAdapter, CourierEvent, address_line, register_courier


def _taka(minor: int) -> int:
    return (minor + 50) // 100


def _json(r: httpx.Response, who: str) -> dict:
    try:
        body = r.json()
    except ValueError as exc:
        raise ProviderError(f"{who} returned a non-JSON response (HTTP {r.status_code})") from exc
    if r.status_code >= 400:
        raise ProviderError(f"{who} error HTTP {r.status_code}: {body.get('message') or body}")
    return body


class Pathao(CourierAdapter):
    key = "pathao"
    label = "Pathao Courier"
    capabilities = frozenset({"book", "track", "webhook", "cod"})
    docs_url = "https://merchant.pathao.com/courier/developer-api"
    HOSTS = {"sandbox": "https://courier-api-sandbox.pathao.com", "live": "https://api-hermes.pathao.com"}
    credential_fields = [{"key": "client_id", "label": "Client ID", "secret": False},
                         {"key": "client_secret", "label": "Client secret", "secret": True},
                         {"key": "username", "label": "Merchant email", "secret": False},
                         {"key": "password", "label": "Merchant password", "secret": True},
                         {"key": "webhook_secret", "label": "Webhook secret", "secret": True}]
    settings_fields = [{"key": "store_id", "label": "Pathao store ID"},
                       {"key": "default_city_id", "label": "Default city ID"},
                       {"key": "default_zone_id", "label": "Default zone ID"}]
    # order_status_slug → canonical (from Pathao merchant docs / SDKs; vendor list UNCONFIRMED against live API)
    status_map = {
        "Pending": "booked", "Pickup_Requested": "booked", "Assigned_for_Pickup": "booked",
        "Pickup_Failed": "booked", "Picked": "picked_up", "At_the_Sorting_HUB": "in_transit",
        "In_Transit": "in_transit", "Received_at_Last_Mile_HUB": "in_transit",
        "Assigned_for_Delivery": "out_for_delivery", "Delivered": "delivered", "Partial_Delivery": "delivered",
        "Delivery_Failed": "failed_attempt", "On_Hold": "failed_attempt", "Return": "returning",
        "Paid_Return": "returned", "Pickup_Cancelled": "cancelled",
    }

    def base(self, ctx) -> str:
        return self.HOSTS["live" if ctx.mode == "live" else "sandbox"]

    def token(self, c: httpx.Client, ctx, cred: dict) -> str:
        body = _json(c.post(self.base(ctx) + "/aladdin/api/v1/issue-token", json={
            "client_id": cred.get("client_id", ""), "client_secret": cred.get("client_secret", ""),
            "username": cred.get("username", ""), "password": cred.get("password", ""), "grant_type": "password"}),
            "Pathao")
        if not body.get("access_token"):
            raise ProviderError("Pathao did not issue an access token")
        return body["access_token"]

    def book(self, ctx, shipment, order, lines) -> dict:
        require_external_network("Pathao")
        cred, st = ctx.credentials(), ctx.settings
        if not st.get("store_id"):
            raise ProviderError("Set the Pathao store ID in the courier settings first")
        payload = {
            "store_id": int(st["store_id"]), "merchant_order_id": order.number,
            "recipient_name": order.name, "recipient_phone": order.phone, "recipient_address": address_line(order),
            "delivery_type": 48, "item_type": 2, "item_quantity": sum(q for _, q in lines),
            "item_weight": max(0.5, round(shipment.weight_grams / 1000, 1)),  # kilograms
            "amount_to_collect": _taka(shipment.cod_amount_minor),  # whole taka
            "item_description": f"Order {order.number}"[:200],
        }
        for f in ("city", "zone"):
            v = (shipment_overrides(shipment).get(f"{f}_id") or st.get(f"default_{f}_id"))
            if v:
                payload[f"recipient_{f}"] = int(v)
        with httpx.Client(timeout=30) as c:
            tok = self.token(c, ctx, cred)
            body = _json(c.post(self.base(ctx) + "/aladdin/api/v1/orders", json=payload,
                                headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"}), "Pathao")
        data = body.get("data") or {}
        if not data.get("consignment_id"):
            raise ProviderError(body.get("message") or "Pathao did not return a consignment id")
        return {"tracking_code": data["consignment_id"], "status": "booked", "tracking_url": None,
                "raw": {"order_status": data.get("order_status"), "delivery_fee": data.get("delivery_fee")}}

    def track(self, ctx, shipment) -> list[CourierEvent]:
        require_external_network("Pathao")
        with httpx.Client(timeout=20) as c:
            tok = self.token(c, ctx, ctx.credentials())
            body = _json(c.get(self.base(ctx) + f"/aladdin/api/v1/orders/{shipment.tracking_code}/info",
                               headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"}), "Pathao")
        data = body.get("data") or {}
        slug = data.get("order_status_slug") or data.get("order_status") or ""
        mapped = self.map_status(slug)
        if not mapped:
            return []
        return [CourierEvent(shipment.tracking_code, mapped, slug, f"Pathao: {slug}",
                             f"poll:{shipment.tracking_code}:{slug}:{data.get('updated_at', '')}")]

    def parse_webhook(self, ctx, headers, raw, query) -> list[CourierEvent]:
        import json

        secret = ctx.credentials().get("webhook_secret")
        if not secret or not hmac.compare_digest(headers.get("x-pathao-signature", ""), secret):
            raise InvalidSignature("Pathao webhook secret does not match")
        body = json.loads(raw)
        if body.get("event") == "webhook_integration":
            return []
        slug = body.get("order_status_slug") or body.get("order_status") or ""
        mapped = self.map_status(slug) or self.map_status((body.get("event") or "").replace("order.", ""))
        if not mapped or not body.get("consignment_id"):
            return []
        return [CourierEvent(body["consignment_id"], mapped, slug, f"Pathao: {slug or body.get('event')}",
                             f"wh:{body['consignment_id']}:{body.get('event')}:{body.get('updated_at', '')}",
                             raw={k: body.get(k) for k in ("event", "consignment_id", "order_status_slug",
                                                           "updated_at")})]

    def verify_credentials(self, ctx) -> tuple[bool, str]:
        require_external_network("Pathao")
        with httpx.Client(timeout=20) as c:
            self.token(c, ctx, ctx.credentials())
        return True, "Pathao issued an access token for these credentials."


def shipment_overrides(shipment) -> dict:
    return dict(shipment.provider_data or {})


class Steadfast(CourierAdapter):
    key = "steadfast"
    label = "Steadfast Courier"
    capabilities = frozenset({"book", "track", "webhook", "cod"})
    docs_url = "https://portal.packzy.com/api/v1"
    BASE = "https://portal.packzy.com/api/v1"  # Steadfast publishes no separate sandbox host
    credential_fields = [{"key": "api_key", "label": "API key", "secret": False},
                         {"key": "secret_key", "label": "Secret key", "secret": True},
                         {"key": "webhook_token", "label": "Webhook bearer token", "secret": True}]
    status_map = {
        "in_review": "booked", "pending": "in_transit", "hold": "failed_attempt", "unknown": "in_transit",
        "delivered_approval_pending": "delivered", "partial_delivered_approval_pending": "delivered",
        "delivered": "delivered", "partial_delivered": "delivered",
        "cancelled_approval_pending": "returning", "cancelled": "returned", "unknown_approval_pending": "in_transit",
    }

    def _headers(self, cred: dict) -> dict:
        return {"Api-Key": cred.get("api_key", ""), "Secret-Key": cred.get("secret_key", ""),
                "Content-Type": "application/json"}

    def book(self, ctx, shipment, order, lines) -> dict:
        require_external_network("Steadfast")
        with httpx.Client(timeout=30) as c:
            body = _json(c.post(self.BASE + "/create_order", headers=self._headers(ctx.credentials()), json={
                "invoice": order.number, "recipient_name": order.name[:100], "recipient_phone": order.phone,
                "recipient_address": address_line(order)[:250], "cod_amount": _taka(shipment.cod_amount_minor),
                "note": (shipment.note or "")[:200]}), "Steadfast")
        cons = body.get("consignment") or {}
        if body.get("status") != 200 or not cons.get("consignment_id"):
            raise ProviderError(body.get("message") or "Steadfast did not create the consignment")
        return {"tracking_code": str(cons["consignment_id"]), "status": "booked",
                "tracking_url": f"https://steadfast.com.bd/t/{cons.get('tracking_code')}" if cons.get("tracking_code")
                else None, "raw": {"tracking_code": cons.get("tracking_code"), "status": cons.get("status")}}

    def track(self, ctx, shipment) -> list[CourierEvent]:
        require_external_network("Steadfast")
        with httpx.Client(timeout=20) as c:
            body = _json(c.get(self.BASE + f"/status_by_cid/{shipment.tracking_code}",
                               headers=self._headers(ctx.credentials())), "Steadfast")
        st = body.get("delivery_status") or ""
        mapped = self.map_status(st)
        return [CourierEvent(shipment.tracking_code, mapped, st, f"Steadfast: {st}",
                             f"poll:{shipment.tracking_code}:{st}")] if mapped else []

    def parse_webhook(self, ctx, headers, raw, query) -> list[CourierEvent]:
        import json

        token = ctx.credentials().get("webhook_token")
        if not token or not hmac.compare_digest(headers.get("authorization", ""), f"Bearer {token}"):
            raise InvalidSignature("Steadfast webhook bearer token does not match")
        body = json.loads(raw)
        if body.get("notification_type") != "delivery_status":
            return []
        st = str(body.get("status") or "").lower()
        mapped = self.map_status(st)
        if not mapped or not body.get("consignment_id"):
            return []
        return [CourierEvent(str(body["consignment_id"]), mapped, st, body.get("tracking_message") or st,
                             f"wh:{body['consignment_id']}:{st}:{body.get('updated_at', '')}",
                             raw={k: body.get(k) for k in ("consignment_id", "invoice", "status", "cod_amount",
                                                           "updated_at")})]

    def verify_credentials(self, ctx) -> tuple[bool, str]:
        require_external_network("Steadfast")
        with httpx.Client(timeout=20) as c:
            body = _json(c.get(self.BASE + "/get_balance", headers=self._headers(ctx.credentials())), "Steadfast")
        if body.get("status") == 200:
            return True, "Steadfast accepted the API key (balance endpoint)."
        return False, f"Steadfast responded {body.get('status')}"


class RedX(CourierAdapter):
    key = "redx"
    label = "RedX"
    capabilities = frozenset({"book", "track", "webhook", "cod"})
    docs_url = "https://redx.com.bd/developer-api/"
    HOSTS = {"sandbox": "https://sandbox.redx.com.bd/v1.0.0-beta", "live": "https://openapi.redx.com.bd/v1.0.0-beta"}
    credential_fields = [{"key": "api_token", "label": "API access token", "secret": True},
                         {"key": "webhook_token", "label": "Webhook URL token (?token=)", "secret": True}]
    settings_fields = [{"key": "pickup_store_id", "label": "Pickup store ID"},
                       {"key": "default_area_id", "label": "Default delivery area ID"}]
    status_map = {
        "pickup-pending": "booked", "ready-for-delivery": "in_transit", "delivery-in-progress": "out_for_delivery",
        "delivered": "delivered", "agent-hold": "failed_attempt", "agent-returning": "returning",
        "returned": "returned", "agent-area-change": "in_transit",
    }

    def base(self, ctx) -> str:
        return self.HOSTS["live" if ctx.mode == "live" else "sandbox"]

    def _headers(self, cred: dict) -> dict:
        return {"API-ACCESS-TOKEN": f"Bearer {cred.get('api_token', '')}", "Content-Type": "application/json"}

    def book(self, ctx, shipment, order, lines) -> dict:
        require_external_network("RedX")
        st = ctx.settings
        a = order.shipping_address or {}
        area_id = shipment_overrides(shipment).get("area_id") or st.get("default_area_id")
        if not area_id:
            raise ProviderError("RedX needs a delivery area ID (set a default in courier settings)")
        payload = {"customer_name": order.name, "customer_phone": order.phone,
                   "delivery_area": a.get("upazila") or a.get("district", ""), "delivery_area_id": int(area_id),
                   "customer_address": address_line(order), "merchant_invoice_id": order.number,
                   "cash_collection_amount": str(_taka(shipment.cod_amount_minor)),
                   "parcel_weight": int(shipment.weight_grams), "value": str(_taka(order.total_minor)),
                   "instruction": (shipment.note or "")[:200], "is_closed_box": False}
        if st.get("pickup_store_id"):
            payload["pickup_store_id"] = int(st["pickup_store_id"])
        with httpx.Client(timeout=30) as c:
            body = _json(c.post(self.base(ctx) + "/parcel", json=payload, headers=self._headers(ctx.credentials())),
                         "RedX")
        if not body.get("tracking_id"):
            raise ProviderError(body.get("message") or "RedX did not return a tracking id")
        return {"tracking_code": str(body["tracking_id"]), "status": "booked",
                "tracking_url": f"https://redx.com.bd/track-parcel/?trackingId={body['tracking_id']}", "raw": {}}

    def track(self, ctx, shipment) -> list[CourierEvent]:
        require_external_network("RedX")
        with httpx.Client(timeout=20) as c:
            body = _json(c.get(self.base(ctx) + f"/parcel/info/{shipment.tracking_code}",
                               headers=self._headers(ctx.credentials())), "RedX")
        st = ((body.get("parcel") or {}).get("status") or "").lower()
        mapped = self.map_status(st)
        return [CourierEvent(shipment.tracking_code, mapped, st, f"RedX: {st}",
                             f"poll:{shipment.tracking_code}:{st}")] if mapped else []

    def parse_webhook(self, ctx, headers, raw, query) -> list[CourierEvent]:
        import json

        token = ctx.credentials().get("webhook_token")
        if not token or not hmac.compare_digest(query.get("token", ""), token):
            raise InvalidSignature("RedX webhook token does not match")
        body = json.loads(raw)
        st = str(body.get("status") or "").lower()
        mapped = self.map_status(st)
        if not mapped or not body.get("tracking_number"):
            return []
        return [CourierEvent(str(body["tracking_number"]), mapped, st, body.get("message_en") or st,
                             f"wh:{body['tracking_number']}:{st}:{body.get('timestamp', '')}",
                             raw={k: body.get(k) for k in ("tracking_number", "status", "timestamp",
                                                           "invoice_number")})]

    def verify_credentials(self, ctx) -> tuple[bool, str]:
        require_external_network("RedX")
        with httpx.Client(timeout=20) as c:
            _json(c.get(self.base(ctx) + "/areas", headers=self._headers(ctx.credentials())), "RedX")
        return True, "RedX accepted the API token (areas endpoint)."


for _c in (Pathao(), Steadfast(), RedX()):
    register_courier(_c)

CATALOG = [
    {"key": "sandbox", "label": "VOLTORA Sandbox Courier", "base_state": "simulation",
     "notes": "Simulated booking and signed status webhooks for testing fulfilment, failed delivery and RTO."},
    {"key": "pathao", "label": "Pathao Courier", "base_state": "adapter_draft_unverified",
     "notes": "Booking, tracking and webhook parsing drafted from Pathao's merchant API docs."},
    {"key": "steadfast", "label": "Steadfast Courier", "base_state": "adapter_draft_unverified",
     "notes": "Booking/tracking/webhook drafted from Steadfast (packzy) API docs. No vendor sandbox host."},
    {"key": "redx", "label": "RedX", "base_state": "adapter_draft_unverified",
     "notes": "Booking/tracking/webhook drafted from RedX open API docs. Requires area IDs from RedX."},
    {"key": "ecourier", "label": "eCourier", "base_state": "catalog_only", "notes": "No adapter yet."},
    {"key": "paperfly", "label": "Paperfly", "base_state": "catalog_only", "notes": "No adapter yet."},
    {"key": "carrybee", "label": "CarryBee", "base_state": "catalog_only", "notes": "No adapter yet."},
]
