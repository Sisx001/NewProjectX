"""Built-in methods: cash on delivery, manual bank transfer and the VOLTORA Sandbox Gateway (a simulation)."""
from __future__ import annotations

import json
import uuid

from ....core.errors import AppError
from .base import (
    SIGNATURE_HEADER,
    InvalidSignature,
    PaymentProvider,
    ProviderContext,
    ProviderEvent,
    RefundResult,
    ensure_secret,
    register_provider,
    verify_signature,
)

SANDBOX_SECRET_REF = "payments.sandbox.webhook_secret"


class CashOnDelivery(PaymentProvider):
    key = "cod"
    label = "Cash on delivery"
    kind = "cod"
    capabilities = frozenset({"manual_refund"})
    description = "Pay the courier in cash when your order arrives."


class BankTransfer(PaymentProvider):
    key = "bank_transfer"
    label = "Bank transfer"
    kind = "manual"
    capabilities = frozenset({"manual_refund", "customer_reference"})
    description = "Transfer to our bank account; we confirm your order once staff verify the deposit."


class SandboxGateway(PaymentProvider):
    """Simulates a hosted payment page plus HMAC-signed webhooks. Never touches real money."""

    key = "sandbox"
    label = "VOLTORA Sandbox Gateway (simulation)"
    kind = "redirect"
    capabilities = frozenset({"webhook", "refund", "partial_refund"})
    simulation = True
    description = "Test payments only — a simulated hosted page. No real money moves."

    def create_intent(self, ctx: ProviderContext, intent, order) -> dict:
        intent.provider_ref = f"sbx_pi_{intent.id.hex[:20]}"
        intent.redirect_url = f"/pay/sandbox/{intent.id}"
        return {"type": "redirect", "url": intent.redirect_url}

    def parse_webhook(self, ctx: ProviderContext, headers: dict[str, str], raw: bytes) -> ProviderEvent:
        secret = ensure_secret(ctx.db, ctx.store_id, SANDBOX_SECRET_REF, "payments.sandbox")
        verify_signature(secret, headers.get(SIGNATURE_HEADER), raw)
        try:
            body = json.loads(raw)
            intent_id = uuid.UUID(body["intent_id"])
            return ProviderEvent(event_id=str(body["id"]), type=str(body["type"]), intent_id=intent_id,
                                 provider_ref=body.get("provider_ref"), amount_minor=int(body["amount_minor"]),
                                 currency=str(body["currency"]), provider_txn_id=body.get("provider_txn_id"),
                                 livemode=bool(body.get("livemode")), reason=body.get("reason"), raw=body)
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidSignature(f"Signed payload is malformed: {exc}") from exc

    def refund(self, ctx: ProviderContext, intent, amount_minor: int, idempotency_key: str) -> RefundResult:
        if amount_minor > intent.captured_minor - intent.refunded_minor:
            raise AppError("over_refund", "Sandbox gateway rejected the refund: amount exceeds captured funds")
        return RefundResult("succeeded", f"sbx_rf_{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:20]}",
                            "Simulated refund")

    def verify_credentials(self, ctx: ProviderContext) -> tuple[bool, str]:
        ensure_secret(ctx.db, ctx.store_id, SANDBOX_SECRET_REF, "payments.sandbox")
        return True, "Simulation ready: webhook signing secret present."


for _p in (CashOnDelivery(), BankTransfer(), SandboxGateway()):
    register_provider(_p)


def parse_raw_headers(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in dict(headers).items()}


__all__ = ["SANDBOX_SECRET_REF", "InvalidSignature", "parse_raw_headers"]
