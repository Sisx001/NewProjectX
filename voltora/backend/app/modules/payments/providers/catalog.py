"""Preloaded payment provider catalog. Metadata ≠ integration: `state` says honestly what exists.

States: built_in (no third party) · simulation · catalog_only (no adapter in VOLTORA) ·
adapter_draft_unverified (code exists, never verified against the provider) · sandbox_verified · live_verified.
The verified states are only reached through a successful credential verification recorded per store.
"""
from __future__ import annotations

CATALOG: list[dict] = [
    {"key": "cod", "label": "Cash on delivery", "kind": "cod", "region": "BD", "base_state": "built_in",
     "credential_fields": [], "docs_url": None,
     "notes": "Collected by the courier on delivery; reconcile remittances under Money → COD."},
    {"key": "bank_transfer", "label": "Bank transfer", "kind": "manual", "region": "BD", "base_state": "built_in",
     "credential_fields": [], "docs_url": None,
     "notes": "Customer transfers and submits a reference; staff verify the deposit before the order is confirmed."},
    {"key": "sandbox", "label": "VOLTORA Sandbox Gateway", "kind": "redirect", "region": "—",
     "base_state": "simulation", "credential_fields": [], "docs_url": None,
     "notes": "Simulated hosted page + signed webhooks for testing. Only available when payments mode is sandbox."},
    {"key": "sslcommerz", "label": "SSLCOMMERZ", "kind": "redirect", "region": "BD",
     "base_state": "adapter_draft_unverified", "docs_url": "https://developer.sslcommerz.com/",
     "credential_fields": [{"key": "store_id", "label": "Store ID", "secret": False},
                           {"key": "store_passwd", "label": "Store password", "secret": True}],
     "notes": "Session + validation + refund APIs drafted from public docs; contract-tested request shapes only."},
    {"key": "bkash", "label": "bKash (tokenized checkout)", "kind": "redirect", "region": "BD",
     "base_state": "adapter_draft_unverified", "docs_url": "https://developer.bka.sh/",
     "credential_fields": [{"key": "app_key", "label": "App key", "secret": False},
                           {"key": "app_secret", "label": "App secret", "secret": True},
                           {"key": "username", "label": "Username", "secret": True},
                           {"key": "password", "label": "Password", "secret": True}],
     "notes": "Grant/create/execute/query/refund drafted from developer.bka.sh; requires a bKash merchant account."},
    {"key": "nagad", "label": "Nagad merchant", "kind": "redirect", "region": "BD", "base_state": "catalog_only",
     "docs_url": "https://nagad.com.bd/", "credential_fields": [],
     "notes": "No adapter yet. Nagad uses RSA-signed requests; needs merchant onboarding and key exchange."},
    {"key": "aamarpay", "label": "aamarPay", "kind": "redirect", "region": "BD", "base_state": "catalog_only",
     "docs_url": "https://aamarpay.com/", "credential_fields": [], "notes": "No adapter yet."},
    {"key": "shurjopay", "label": "shurjoPay", "kind": "redirect", "region": "BD", "base_state": "catalog_only",
     "docs_url": "https://shurjopay.com.bd/", "credential_fields": [], "notes": "No adapter yet."},
    {"key": "uddoktapay", "label": "UddoktaPay", "kind": "redirect", "region": "BD", "base_state": "catalog_only",
     "docs_url": "https://uddoktapay.com/", "credential_fields": [], "notes": "No adapter yet."},
    {"key": "stripe", "label": "Stripe", "kind": "redirect", "region": "International",
     "base_state": "catalog_only", "docs_url": "https://stripe.com/docs",
     "credential_fields": [], "notes": "No adapter yet. Stripe does not onboard Bangladesh-registered merchants."},
    {"key": "paypal", "label": "PayPal", "kind": "redirect", "region": "International",
     "base_state": "catalog_only", "docs_url": "https://developer.paypal.com/",
     "credential_fields": [], "notes": "No adapter yet (legacy adapter never captured payments)."},
]
BY_KEY = {c["key"]: c for c in CATALOG}


def state_for(entry: dict, config) -> str:
    base = entry["base_state"]
    if base != "adapter_draft_unverified" or config is None:
        return base
    v = (config.verification or {}).get("state")
    return v if v in ("sandbox_verified", "live_verified") else base


def enableable(entry: dict, config, payments_mode: str) -> tuple[bool, str | None]:
    state = state_for(entry, config)
    if state == "catalog_only":
        return False, "VOLTORA has no adapter for this provider yet (catalog entry only)."
    if state == "adapter_draft_unverified":
        return False, "Save credentials and run Verify first. Unverified adapters can't take payments."
    if state == "simulation" and payments_mode != "sandbox":
        return False, "The sandbox gateway is only available when the server runs in payments sandbox mode."
    if state == "sandbox_verified" and payments_mode == "live":
        return False, "Verified in sandbox only. Verify live credentials before enabling in live mode."
    return True, None
