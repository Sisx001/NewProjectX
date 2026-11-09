"""Payment provider contract and registry. Plugins register additional providers with `register_provider`."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....config import get_settings
from ....core.errors import AppError
from ....core.security import consteq, hmac_sha256, new_token, seal, unseal

SIGNATURE_HEADER = "x-voltora-signature"
TOLERANCE_SECONDS = 300


class InvalidSignature(Exception):
    pass


class ProviderUnavailable(AppError):
    status = 503

    def __init__(self, message: str):
        super().__init__("provider_unavailable", message)


class ProviderError(AppError):
    status = 502

    def __init__(self, message: str, details: Any = None):
        super().__init__("provider_error", message, details=details)


def require_external_network(label: str) -> None:
    if not get_settings().external_network:
        raise ProviderUnavailable(f"{label} cannot be reached: external network access is disabled on this server "
                                  "(VOLTORA_EXTERNAL_NETWORK=false). Nothing was sent.")


@dataclass
class ProviderContext:
    db: Session
    store_id: uuid.UUID
    key: str
    mode: str = "sandbox"
    config: Any = None  # PaymentMethodConfig | None
    secret_prefix: str = "payments"

    def credentials(self) -> dict[str, str]:
        from ...platform.models import SecretRecord

        prefix = f"{self.secret_prefix}.{self.key}."
        rows = self.db.execute(select(SecretRecord).where(SecretRecord.store_id == self.store_id,
                                                          SecretRecord.ref.startswith(prefix))).scalars()
        return {r.ref[len(prefix):]: unseal(r.sealed) for r in rows}

    @property
    def settings(self) -> dict:
        return dict(getattr(self.config, "settings", None) or {})

    @property
    def public_url(self) -> str:
        return get_settings().public_url.rstrip("/")


@dataclass
class ProviderEvent:
    event_id: str
    type: str  # payment.succeeded|payment.failed|payment.cancelled
    intent_id: uuid.UUID | None = None
    provider_ref: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    provider_txn_id: str | None = None
    livemode: bool | None = None
    reason: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class RefundResult:
    status: str  # succeeded|processing|failed
    provider_refund_id: str | None = None
    message: str | None = None


class PaymentProvider:
    key: ClassVar[str]
    label: ClassVar[str]
    kind: ClassVar[str]  # cod|redirect|manual
    capabilities: ClassVar[frozenset[str]] = frozenset()  # refund|partial_refund|webhook|query|redirect_return
    simulation: ClassVar[bool] = False
    description: ClassVar[str] = ""

    def create_intent(self, ctx: ProviderContext, intent, order) -> dict:
        """Return the customer's next step: {"type": "redirect", "url"} or {"type": "done"}."""
        return {"type": "done"}

    def parse_webhook(self, ctx: ProviderContext, headers: dict[str, str], raw: bytes) -> ProviderEvent:
        raise InvalidSignature(f"{self.label} does not send webhooks")

    def handle_return(self, ctx: ProviderContext, intent, params: dict[str, str]) -> ProviderEvent | None:
        """Customer came back from the hosted page. Must confirm server-to-server; never trust params."""
        return None

    def query(self, ctx: ProviderContext, intent) -> ProviderEvent | None:
        return None

    def refund(self, ctx: ProviderContext, intent, amount_minor: int, idempotency_key: str) -> RefundResult:
        raise AppError("refund_unsupported", f"{self.label} refunds must be paid out manually", status=409)

    def verify_credentials(self, ctx: ProviderContext) -> tuple[bool, str]:
        return False, "This provider has no credential check."


PROVIDERS: dict[str, PaymentProvider] = {}


def register_provider(p: PaymentProvider) -> PaymentProvider:
    PROVIDERS[p.key] = p
    return p


def get_provider(key: str) -> PaymentProvider | None:
    return PROVIDERS.get(key)


# ---------------------------------------------------------------- HMAC webhook signatures (sandbox gateway/courier)
def sign(secret: str, body: bytes | str, ts: int | None = None) -> str:
    ts = int(time.time()) if ts is None else ts
    b = body.decode() if isinstance(body, bytes) else body
    return f"t={ts},v1={hmac_sha256(secret, f'{ts}.{b}')}"


def verify_signature(secret: str, header: str | None, body: bytes, now: float | None = None,
                     tolerance: int = TOLERANCE_SECONDS) -> None:
    if not header:
        raise InvalidSignature("Missing signature header")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts.get("t", ""))
    except ValueError as exc:
        raise InvalidSignature("Malformed signature header") from exc
    now = time.time() if now is None else now
    if abs(now - ts) > tolerance:
        raise InvalidSignature("Signature timestamp outside the allowed window")
    expected = hmac_sha256(secret, f"{ts}.{body.decode(errors='replace')}")
    if not consteq(expected, parts.get("v1", "")):
        raise InvalidSignature("Signature does not match")


def ensure_secret(db: Session, store_id: uuid.UUID, ref: str, owner_ref: str) -> str:
    """Get or create a sealed random secret (used for sandbox webhook signing)."""
    from sqlalchemy.dialects.postgresql import insert

    from ...platform.models import SecretRecord

    raw = new_token(32)
    db.execute(insert(SecretRecord).values(id=uuid.uuid4(), store_id=store_id, ref=ref, sealed=seal(raw), hint=None,
                                           owner_kind="core", owner_ref=owner_ref)
               .on_conflict_do_nothing(index_elements=["store_id", "ref"]))
    rec = db.execute(select(SecretRecord).where(SecretRecord.store_id == store_id, SecretRecord.ref == ref)
                     ).scalar_one()
    return unseal(rec.sealed)
