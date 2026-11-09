"""Courier adapter contract + registry (plugins register more with `register_courier`)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import ClassVar

from ...payments.providers.base import InvalidSignature, ProviderContext  # shared context/errors

SHIPMENT_TRANSITIONS: dict[str, set[str]] = {
    "booked": {"picked_up", "in_transit", "cancelled"},
    "picked_up": {"in_transit", "out_for_delivery", "delivered", "failed_attempt", "returning"},
    "in_transit": {"out_for_delivery", "delivered", "failed_attempt", "returning"},
    "out_for_delivery": {"delivered", "failed_attempt", "returning", "in_transit"},
    "failed_attempt": {"out_for_delivery", "in_transit", "delivered", "returning"},
    "returning": {"returned"},
    "delivered": set(),
    "returned": set(),
    "cancelled": set(),
}
SHIPMENT_LABELS = {"booked": "Booked with courier", "picked_up": "Picked up", "in_transit": "In transit",
                   "out_for_delivery": "Out for delivery", "delivered": "Delivered",
                   "failed_attempt": "Delivery attempt failed", "returning": "Returning to sender",
                   "returned": "Returned to sender", "cancelled": "Shipment cancelled"}


@dataclass
class CourierEvent:
    tracking_code: str
    status: str  # canonical (SHIPMENT_TRANSITIONS keys)
    provider_status: str = ""
    message: str = ""
    event_id: str | None = None
    at: dt.datetime | None = None
    raw: dict = field(default_factory=dict)


class CourierAdapter:
    key: ClassVar[str]
    label: ClassVar[str]
    simulation: ClassVar[bool] = False
    capabilities: ClassVar[frozenset[str]] = frozenset()  # book|track|cancel|webhook|cod
    credential_fields: ClassVar[list[dict]] = []
    settings_fields: ClassVar[list[dict]] = []
    status_map: ClassVar[dict[str, str]] = {}
    docs_url: ClassVar[str | None] = None

    def map_status(self, provider_status: str) -> str | None:
        return self.status_map.get(provider_status) or self.status_map.get(provider_status.lower())

    def book(self, ctx: ProviderContext, shipment, order, lines: list) -> dict:
        raise NotImplementedError

    def track(self, ctx: ProviderContext, shipment) -> list[CourierEvent]:
        return []

    def cancel(self, ctx: ProviderContext, shipment) -> None:
        raise NotImplementedError

    def parse_webhook(self, ctx: ProviderContext, headers: dict[str, str], raw: bytes,
                      query: dict[str, str]) -> list[CourierEvent]:
        raise InvalidSignature(f"{self.label} webhooks are not supported")

    def verify_credentials(self, ctx: ProviderContext) -> tuple[bool, str]:
        return False, "No credential check available"


COURIERS: dict[str, CourierAdapter] = {}


def register_courier(c: CourierAdapter) -> CourierAdapter:
    COURIERS[c.key] = c
    return c


def address_line(order) -> str:
    a = order.shipping_address or {}
    parts = [a.get("line1"), a.get("line2"), a.get("area"), a.get("upazila"), a.get("district")]
    return ", ".join(p for p in parts if p)
