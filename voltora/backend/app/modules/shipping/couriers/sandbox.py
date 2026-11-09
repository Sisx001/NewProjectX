"""VOLTORA Sandbox Courier — a labelled simulation with HMAC-signed status webhooks."""
from __future__ import annotations

import datetime as dt
import json
import uuid

from ...payments.providers.base import SIGNATURE_HEADER, InvalidSignature, ensure_secret, verify_signature
from .base import CourierAdapter, CourierEvent, register_courier

SANDBOX_COURIER_SECRET_REF = "shipping.sandbox.webhook_secret"


class SandboxCourier(CourierAdapter):
    key = "sandbox"
    label = "VOLTORA Sandbox Courier (simulation)"
    simulation = True
    capabilities = frozenset({"book", "cancel", "webhook", "cod"})
    status_map = {s: s for s in ("picked_up", "in_transit", "out_for_delivery", "delivered", "failed_attempt",
                                 "returning", "returned", "cancelled")}

    def book(self, ctx, shipment, order, lines) -> dict:
        code = f"SBX-{uuid.uuid4().hex[:8].upper()}"
        return {"tracking_code": code, "status": "booked", "tracking_url": None, "raw": {"simulation": True}}

    def cancel(self, ctx, shipment) -> None:
        return None

    def parse_webhook(self, ctx, headers, raw, query) -> list[CourierEvent]:
        secret = ensure_secret(ctx.db, ctx.store_id, SANDBOX_COURIER_SECRET_REF, "shipping.sandbox")
        verify_signature(secret, headers.get(SIGNATURE_HEADER), raw)
        try:
            body = json.loads(raw)
            return [CourierEvent(tracking_code=body["tracking_code"], status=body["status"],
                                 provider_status=body["status"], message=body.get("message", ""),
                                 event_id=body["id"], at=dt.datetime.fromtimestamp(body["created"], dt.UTC),
                                 raw=body)]
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidSignature(f"Malformed sandbox courier payload: {exc}") from exc

    def verify_credentials(self, ctx) -> tuple[bool, str]:
        return True, "Simulation: no credentials required."


register_courier(SandboxCourier())
