"""bKash Tokenized Checkout — ADAPTER DRAFT, UNVERIFIED.

Written against developer.bka.sh (tokenized checkout v1.2.0-beta): grant token, create payment (mode 0011),
execute payment (returns trxID), query payment status, refund. bKash returns the customer to `callbackURL` with
paymentID/status; we always execute + check amount/currency/invoice server-to-server, so the redirect alone never
marks an order paid. Fixes vs legacy: correct `/tokenized/checkout/*` paths, `X-App-Key` header, amount check.
"""
from __future__ import annotations

import httpx

from .base import (
    PaymentProvider,
    ProviderContext,
    ProviderError,
    ProviderEvent,
    RefundResult,
    register_provider,
    require_external_network,
)

HOSTS = {"sandbox": "https://tokenized.sandbox.bka.sh/v1.2.0-beta",
         "live": "https://tokenized.pay.bka.sh/v1.2.0-beta"}


def _amount(minor: int) -> str:
    return f"{minor // 100}.{minor % 100:02d}"


def _minor(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    whole, _, frac = str(value).partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


class Bkash(PaymentProvider):
    key = "bkash"
    label = "bKash"
    kind = "redirect"
    capabilities = frozenset({"refund", "partial_refund", "query", "redirect_return"})
    description = "Pay with your bKash account on bKash's own page."
    credential_fields = ("app_key", "app_secret", "username", "password")

    def base(self, ctx: ProviderContext) -> str:
        return HOSTS["live" if ctx.mode == "live" else "sandbox"]

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=30)

    def grant(self, c: httpx.Client, ctx: ProviderContext, cred: dict) -> str:
        r = c.post(self.base(ctx) + "/tokenized/checkout/token/grant",
                   json={"app_key": cred.get("app_key", ""), "app_secret": cred.get("app_secret", "")},
                   headers={"Content-Type": "application/json", "Accept": "application/json",
                            "username": cred.get("username", ""), "password": cred.get("password", "")})
        body = r.json()
        if not body.get("id_token"):
            raise ProviderError(body.get("statusMessage") or body.get("msg") or "bKash rejected the app credentials")
        return body["id_token"]

    def _headers(self, cred: dict, token: str) -> dict:
        return {"Content-Type": "application/json", "Accept": "application/json", "Authorization": token,
                "X-App-Key": cred.get("app_key", "")}

    def create_intent(self, ctx: ProviderContext, intent, order) -> dict:
        require_external_network("bKash")
        cred = ctx.credentials()
        with self._client() as c:
            token = self.grant(c, ctx, cred)
            r = c.post(self.base(ctx) + "/tokenized/checkout/create", headers=self._headers(cred, token), json={
                "mode": "0011", "payerReference": order.phone,
                "callbackURL": f"{ctx.public_url}/api/payments/return/bkash/{intent.id}",
                "amount": _amount(intent.amount_minor), "currency": intent.currency, "intent": "sale",
                "merchantInvoiceNumber": intent.id.hex[:32]})
        body = r.json()
        if body.get("statusCode") != "0000" or not body.get("bkashURL"):
            raise ProviderError(body.get("statusMessage") or "bKash did not return a payment URL")
        intent.provider_ref = body.get("paymentID")
        intent.redirect_url = body["bkashURL"]
        return {"type": "redirect", "url": body["bkashURL"]}

    def _event(self, intent, body: dict, source: str) -> ProviderEvent:
        status = body.get("transactionStatus")
        if status == "Completed":
            if body.get("merchantInvoiceNumber") and body["merchantInvoiceNumber"] != intent.id.hex[:32]:
                raise ProviderError("bKash payment belongs to a different invoice")
            kind = "payment.succeeded"
        elif status in ("Failed", "Cancelled", "Expired") or body.get("statusCode") not in (None, "0000"):
            kind = "payment.failed"
        else:
            return None  # Initiated / pending
        return ProviderEvent(event_id=f"{source}:{body.get('paymentID') or intent.provider_ref}:{status}", type=kind,
                             intent_id=intent.id, provider_ref=body.get("paymentID"),
                             amount_minor=_minor(body.get("amount")), currency=body.get("currency"),
                             provider_txn_id=body.get("trxID"), reason=body.get("statusMessage"),
                             raw={k: body.get(k) for k in ("paymentID", "trxID", "transactionStatus", "amount",
                                                           "currency", "statusCode", "statusMessage")})

    def handle_return(self, ctx: ProviderContext, intent, params: dict[str, str]) -> ProviderEvent | None:
        if params.get("status") in ("cancel", "failure"):
            return ProviderEvent(event_id=f"return:{intent.provider_ref}:{params.get('status')}",
                                 type="payment.failed" if params["status"] == "failure" else "payment.cancelled",
                                 intent_id=intent.id, reason=f"bKash reported {params['status']}")
        if not intent.provider_ref or params.get("paymentID") != intent.provider_ref:
            raise ProviderError("bKash paymentID does not belong to this payment")
        require_external_network("bKash")
        cred = ctx.credentials()
        with self._client() as c:
            token = self.grant(c, ctx, cred)
            r = c.post(self.base(ctx) + "/tokenized/checkout/execute", headers=self._headers(cred, token),
                       json={"paymentID": intent.provider_ref})
        return self._event(intent, r.json(), "execute")

    def query(self, ctx: ProviderContext, intent) -> ProviderEvent | None:
        require_external_network("bKash")
        cred = ctx.credentials()
        with self._client() as c:
            token = self.grant(c, ctx, cred)
            r = c.post(self.base(ctx) + "/tokenized/checkout/payment/status", headers=self._headers(cred, token),
                       json={"paymentID": intent.provider_ref})
        return self._event(intent, r.json(), "query")

    def refund(self, ctx: ProviderContext, intent, amount_minor: int, idempotency_key: str) -> RefundResult:
        require_external_network("bKash")
        if not intent.provider_ref or not intent.provider_txn_id:
            raise ProviderError("This payment has no bKash paymentID/trxID to refund")
        cred = ctx.credentials()
        with self._client() as c:
            token = self.grant(c, ctx, cred)
            r = c.post(self.base(ctx) + "/tokenized/checkout/payment/refund", headers=self._headers(cred, token),
                       json={"paymentID": intent.provider_ref, "trxID": intent.provider_txn_id,
                             "amount": _amount(amount_minor), "sku": idempotency_key[:32], "reason": "Store refund"})
        body = r.json()
        if body.get("transactionStatus") == "Completed" and body.get("refundTrxID"):
            return RefundResult("succeeded", body["refundTrxID"])
        return RefundResult("failed", None, body.get("statusMessage") or body.get("errorMessage") or "Refund failed")

    def verify_credentials(self, ctx: ProviderContext) -> tuple[bool, str]:
        require_external_network("bKash")
        with self._client() as c:
            self.grant(c, ctx, ctx.credentials())
        return True, "bKash granted a token for these credentials."


register_provider(Bkash())
