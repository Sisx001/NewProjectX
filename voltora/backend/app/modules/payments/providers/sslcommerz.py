"""SSLCOMMERZ hosted checkout — ADAPTER DRAFT, UNVERIFIED.

Written against the public developer docs (developer.sslcommerz.com): session API `POST /gwprocess/v4/api.php`,
validation API `GET /validator/api/validationserverAPI.php`, refund/transaction query
`GET /validator/api/merchantTransIDvalidationAPI.php`. Request shapes are covered by respx contract tests; it has
never talked to SSLCOMMERZ from this environment. IPN bodies are untrusted: every IPN is re-validated server-side.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs

import httpx

from .base import (
    InvalidSignature,
    PaymentProvider,
    ProviderContext,
    ProviderError,
    ProviderEvent,
    RefundResult,
    register_provider,
    require_external_network,
)

HOSTS = {"sandbox": "https://sandbox.sslcommerz.com", "live": "https://securepay.sslcommerz.com"}


def _amount(minor: int) -> str:
    return f"{minor // 100}.{minor % 100:02d}"


def _to_minor(value) -> int:
    try:
        d = Decimal(str(value)) * 100
    except (InvalidOperation, ValueError) as exc:
        raise ProviderError("SSLCOMMERZ returned an invalid amount") from exc
    if d != d.to_integral_value():
        raise ProviderError("SSLCOMMERZ returned a fractional minor amount")
    return int(d)


class SslCommerz(PaymentProvider):
    key = "sslcommerz"
    label = "SSLCOMMERZ"
    kind = "redirect"
    capabilities = frozenset({"webhook", "refund", "partial_refund", "query"})
    description = "Cards, mobile banking and internet banking via SSLCOMMERZ's hosted page."
    credential_fields = ("store_id", "store_passwd")

    def base(self, ctx: ProviderContext) -> str:
        return HOSTS["live" if ctx.mode == "live" else "sandbox"]

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=20)

    def create_intent(self, ctx: ProviderContext, intent, order) -> dict:
        require_external_network("SSLCOMMERZ")
        cred = ctx.credentials()
        addr = order.shipping_address or {}
        back = f"{ctx.public_url}/api/payments/return/sslcommerz/{intent.id}"
        form = {
            "store_id": cred.get("store_id", ""), "store_passwd": cred.get("store_passwd", ""),
            "total_amount": _amount(intent.amount_minor), "currency": intent.currency, "tran_id": intent.id.hex,
            "success_url": back, "fail_url": back, "cancel_url": back,
            "ipn_url": f"{ctx.public_url}/api/payments/webhooks/sslcommerz",
            "cus_name": order.name, "cus_email": order.email or "noreply@example.invalid", "cus_phone": order.phone,
            "cus_add1": addr.get("line1", ""), "cus_city": addr.get("district", ""), "cus_country": "Bangladesh",
            "cus_postcode": addr.get("postal_code", "") or "0000",
            "shipping_method": "Courier", "num_of_item": 1, "ship_name": order.name,
            "ship_add1": addr.get("line1", ""), "ship_city": addr.get("district", ""),
            "ship_postcode": addr.get("postal_code", "") or "0000", "ship_country": "Bangladesh",
            "product_name": f"Order {order.number}", "product_category": "general", "product_profile": "general",
            "value_a": order.number,
        }
        with self._client() as c:
            r = c.post(self.base(ctx) + "/gwprocess/v4/api.php", data=form)
        body = r.json()
        if body.get("status") != "SUCCESS" or not body.get("GatewayPageURL"):
            raise ProviderError(body.get("failedreason") or "SSLCOMMERZ did not create a session")
        intent.provider_ref = body.get("sessionkey")
        intent.redirect_url = body["GatewayPageURL"]
        return {"type": "redirect", "url": body["GatewayPageURL"]}

    def _validate(self, ctx: ProviderContext, val_id: str) -> dict:
        require_external_network("SSLCOMMERZ")
        cred = ctx.credentials()
        with self._client() as c:
            r = c.get(self.base(ctx) + "/validator/api/validationserverAPI.php",
                      params={"val_id": val_id, "store_id": cred.get("store_id", ""),
                              "store_passwd": cred.get("store_passwd", ""), "format": "json"})
        return r.json()

    def _event_from_validation(self, val_id: str, body: dict) -> ProviderEvent:
        status = body.get("status", "")
        kind = ("payment.succeeded" if status in ("VALID", "VALIDATED")
                else "payment.failed" if status in ("FAILED", "CANCELLED", "EXPIRED", "INVALID_TRANSACTION") else "")
        if not kind or not body.get("tran_id"):
            raise InvalidSignature(f"SSLCOMMERZ validation returned status {status or 'unknown'}")
        import uuid

        try:
            intent_id = uuid.UUID(hex=body["tran_id"])
        except ValueError as exc:
            raise InvalidSignature("Unknown tran_id") from exc
        return ProviderEvent(event_id=f"val:{val_id}:{status}", type=kind, intent_id=intent_id,
                             amount_minor=_to_minor(body.get("amount", "0")) if kind == "payment.succeeded" else None,
                             currency=body.get("currency"), provider_txn_id=body.get("bank_tran_id"),
                             raw={k: body.get(k) for k in ("status", "tran_id", "amount", "currency", "bank_tran_id",
                                                           "card_type", "val_id")})

    def parse_webhook(self, ctx: ProviderContext, headers: dict[str, str], raw: bytes) -> ProviderEvent:
        form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "ignore")).items()}
        val_id = form.get("val_id")
        if not val_id:
            raise InvalidSignature("IPN without val_id")
        return self._event_from_validation(val_id, self._validate(ctx, val_id))

    def handle_return(self, ctx: ProviderContext, intent, params: dict[str, str]) -> ProviderEvent | None:
        if not params.get("val_id"):
            return None
        return self._event_from_validation(params["val_id"], self._validate(ctx, params["val_id"]))

    def refund(self, ctx: ProviderContext, intent, amount_minor: int, idempotency_key: str) -> RefundResult:
        require_external_network("SSLCOMMERZ")
        if not intent.provider_txn_id:
            raise ProviderError("This payment has no SSLCOMMERZ bank_tran_id to refund")
        cred = ctx.credentials()
        with self._client() as c:
            r = c.get(self.base(ctx) + "/validator/api/merchantTransIDvalidationAPI.php",
                      params={"bank_tran_id": intent.provider_txn_id, "refund_amount": _amount(amount_minor),
                              "refund_remarks": "Store refund", "refe_id": idempotency_key[:50],
                              "store_id": cred.get("store_id", ""), "store_passwd": cred.get("store_passwd", ""),
                              "format": "json"})
        body = r.json()
        if body.get("APIConnect") != "DONE":
            raise ProviderError(f"SSLCOMMERZ refund API: {body.get('APIConnect') or 'no response'}")
        status = str(body.get("status", "")).lower()
        if status == "success":
            return RefundResult("processing", body.get("refund_ref_id"), "Refund initiated at SSLCOMMERZ")
        if status == "processing":
            return RefundResult("processing", body.get("refund_ref_id"), body.get("errorReason"))
        return RefundResult("failed", None, body.get("errorReason") or status)

    def verify_credentials(self, ctx: ProviderContext) -> tuple[bool, str]:
        require_external_network("SSLCOMMERZ")
        cred = ctx.credentials()
        with self._client() as c:
            r = c.get(self.base(ctx) + "/validator/api/merchantTransIDvalidationAPI.php",
                      params={"tran_id": "voltora-credential-check", "store_id": cred.get("store_id", ""),
                              "store_passwd": cred.get("store_passwd", ""), "format": "json"})
        body = r.json()
        if body.get("APIConnect") in ("DONE",):
            return True, "SSLCOMMERZ accepted the store credentials (transaction query API)."
        return False, f"SSLCOMMERZ responded {body.get('APIConnect') or r.status_code}"


register_provider(SslCommerz())
