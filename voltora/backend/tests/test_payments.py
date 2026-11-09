"""Golden journey 6, 25, 28: sandbox gateway signed webhooks, provider catalog honesty, credentials, adapter drafts."""
from __future__ import annotations

import json
import time
import urllib.parse

import httpx
import pytest
import respx
from sqlalchemy import func, select, text

from tests.conftest import staff_api
from tests.test_commerce_support import add, buy, checkout, intents, level, product, sandbox_event, sandbox_signed


def _sandbox_order(api, owner_api, db, store, **kw):
    res, v = buy(api, db, store, method="sandbox", **kw)
    assert res["order"]["status"] == "awaiting_payment"
    assert res["next"]["type"] == "redirect" and res["next"]["url"].startswith("/pay/sandbox/")
    it = intents(owner_api, res["order"]["number"])[-1]
    return res, v, it


def _ledger_count(db):
    from app.modules.payments.models import LedgerEntry

    db.expire_all()
    return db.execute(select(func.count()).select_from(LedgerEntry)).scalar_one()


def test_sandbox_success_marks_paid_only_via_signed_webhook(api, owner_api, db, store):
    res, v, it = _sandbox_order(api, owner_api, db, store)
    o = res["order"]
    page = api.get(f"/api/payments/sandbox/{it['id']}").json()
    assert page["simulation"] is True and page["amount_minor"] == o["total_minor"] and page["merchant"] == "Test Store"
    r = api.post(f"/api/payments/sandbox/{it['id']}/complete", json={"outcome": "success"})
    assert r.status_code == 200 and f"/order/{o['number']}?token=" in r.json()["redirect_url"]
    view = api.get(f"/api/store/orders/{o['number']}?token={o['access_token']}").json()
    assert view["status"] == "confirmed" and view["payment_status"] == "paid" and view["is_test"] is True
    detail = owner_api.get(f"/api/admin/orders/{o['number']}").json()
    assert detail["payment"]["intents"][0]["status"] == "succeeded"
    assert detail["payment"]["intents"][0]["provider_txn_id"].startswith("SBX")
    assert [e["status"] for e in detail["payment"]["events"]] == ["processed"]
    led = owner_api.get("/api/admin/payments/ledger").json()
    assert led["items"][0]["kind"] == "payment_captured" and led["items"][0]["amount_minor"] == o["total_minor"]
    assert led["items"][0]["mode"] == "sandbox" and led["net_live_minor"] == 0
    assert level(db, v.id) == (5, 1)


def test_sandbox_failure_then_retry(api, owner_api, db, store):
    res, _, it = _sandbox_order(api, owner_api, db, store)
    o = res["order"]
    api.post(f"/api/payments/sandbox/{it['id']}/complete", json={"outcome": "fail"})
    view = api.get(f"/api/store/orders/{o['number']}?token={o['access_token']}").json()
    assert view["status"] == "awaiting_payment" and view["payment"]["can_retry"] is True
    assert api.post(f"/api/payments/sandbox/{it['id']}/complete", json={"outcome": "success"}).status_code == 409
    r = api.post(f"/api/store/orders/{o['number']}/pay?token={o['access_token']}")
    assert r.status_code == 200 and r.json()["next"]["type"] == "redirect"
    new = intents(owner_api, o["number"])[-1]
    assert new["id"] != it["id"] and new["status"] == "requires_action"
    api.post(f"/api/payments/sandbox/{new['id']}/complete", json={"outcome": "success"})
    assert api.get(f"/api/store/orders/{o['number']}?token={o['access_token']}").json()["payment_status"] == "paid"
    assert _ledger_count(db) == 1


def test_duplicate_and_replayed_webhook_is_noop(api, owner_api, db, store):
    res, _, it = _sandbox_order(api, owner_api, db, store)
    raw, h = sandbox_signed(db, store, sandbox_event(it, event_id="evt_dupe_1"))
    first = api.c.post("/api/payments/webhooks/sandbox", content=raw, headers=h)
    assert first.status_code == 200 and first.json()["status"] == "processed"
    for _ in range(2):
        again = api.c.post("/api/payments/webhooks/sandbox", content=raw, headers=h)
        assert again.status_code == 200 and again.json()["status"] == "duplicate"
    # a different event id for the same already-captured intent never double counts
    raw2, h2 = sandbox_signed(db, store, sandbox_event(it, event_id="evt_dupe_2"))
    assert api.c.post("/api/payments/webhooks/sandbox", content=raw2, headers=h2).json()["reason"] == "Already captured"
    assert _ledger_count(db) == 1
    detail = owner_api.get(f"/api/admin/orders/{res['order']['number']}").json()
    assert detail["totals"]["paid_minor"] == res["order"]["total_minor"]


def test_tampered_stale_and_mismatched_webhooks_rejected(api, owner_api, db, store):
    from app.modules.payments.models import PaymentEvent

    res, _, it = _sandbox_order(api, owner_api, db, store)
    raw, h = sandbox_signed(db, store, sandbox_event(it))
    tampered = raw.replace(str(it["amount_minor"]), str(it["amount_minor"] - 100))
    r = api.c.post("/api/payments/webhooks/sandbox", content=tampered, headers=h)
    assert r.status_code == 400 and r.json()["status"] == "rejected"
    r = api.c.post("/api/payments/webhooks/sandbox", content=raw, headers={**h, "x-voltora-signature": "t=1,v1=00"})
    assert r.status_code == 400
    old_raw, old_h = sandbox_signed(db, store, sandbox_event(it), ts=int(time.time()) - 3600)
    r = api.c.post("/api/payments/webhooks/sandbox", content=old_raw, headers=old_h)
    assert r.status_code == 400 and "window" in r.json()["reason"]
    bad_amount, bh = sandbox_signed(db, store, sandbox_event(it, amount=it["amount_minor"] - 1))
    r = api.c.post("/api/payments/webhooks/sandbox", content=bad_amount, headers=bh)
    assert r.status_code == 422 and r.json()["status"] == "rejected"
    bad_cur, ch = sandbox_signed(db, store, sandbox_event(it, currency="USD"))
    assert api.c.post("/api/payments/webhooks/sandbox", content=bad_cur, headers=ch).status_code == 422
    other = {**sandbox_event(it), "intent_id": "8d0c6a3e-1f43-4f0f-9a43-3a3f0f6e2b11"}
    oraw, oh = sandbox_signed(db, store, other)
    assert api.c.post("/api/payments/webhooks/sandbox", content=oraw, headers=oh).status_code == 422
    view = api.get(f"/api/store/orders/{res['order']['number']}?token={res['order']['access_token']}").json()
    assert view["payment_status"] == "unpaid" and view["status"] == "awaiting_payment"
    assert _ledger_count(db) == 0
    db.expire_all()
    invalid = db.execute(select(PaymentEvent).where(PaymentEvent.signature_valid.is_(False))).scalars().all()
    assert len(invalid) == 3 and all(e.status == "rejected" for e in invalid)


def test_failed_processing_is_retried_not_deduped(api, owner_api, db, store, monkeypatch):
    from app.modules.payments import service as svc

    res, _, it = _sandbox_order(api, owner_api, db, store)
    raw, h = sandbox_signed(db, store, sandbox_event(it, event_id="evt_retry"))
    real = svc.apply_event
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        return real(*a, **kw)

    monkeypatch.setattr(svc, "apply_event", flaky)
    r = api.c.post("/api/payments/webhooks/sandbox", content=raw, headers=h)
    assert r.status_code == 500
    r = api.c.post("/api/payments/webhooks/sandbox", content=raw, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "processed"
    assert owner_api.get(f"/api/admin/orders/{res['order']['number']}").json()["payment_status"] == "paid"


def test_timeout_redirect_never_pays_and_order_expires(api, owner_api, db, store):
    from app.modules.commerce.service import expire_unpaid_orders
    from app.db import utcnow

    res, v, it = _sandbox_order(api, owner_api, db, store)
    o = res["order"]
    r = api.post(f"/api/payments/sandbox/{it['id']}/complete", json={"outcome": "timeout"})
    assert r.status_code == 200
    view = api.get(f"/api/store/orders/{o['number']}?token={o['access_token']}").json()
    assert view["payment_status"] == "unpaid" and view["status"] == "awaiting_payment"
    import datetime as dt

    assert expire_unpaid_orders(db, utcnow() + dt.timedelta(minutes=5)) == 0  # not yet due
    assert expire_unpaid_orders(db, utcnow() + dt.timedelta(hours=2)) == 1
    db.commit()
    assert level(db, v.id) == (5, 0)
    view = api.get(f"/api/store/orders/{o['number']}?token={o['access_token']}").json()
    assert view["status"] == "cancelled" and view["payment_status"] == "voided"
    # a late signed success is accepted (money moved) but flags a refund instead of reviving the order
    raw, h = sandbox_signed(db, store, sandbox_event(it))
    assert api.c.post("/api/payments/webhooks/sandbox", content=raw, headers=h).json()["status"] == "processed"
    rec = owner_api.get("/api/admin/payments/reconciliation").json()
    assert any("refund required" in i["problem"] for i in rec["issues"])
    assert owner_api.get(f"/api/admin/orders/{o['number']}").json()["status"] == "cancelled"


def test_ledger_is_append_only(api, owner_api, db, store):
    res, _, it = _sandbox_order(api, owner_api, db, store)
    api.post(f"/api/payments/sandbox/{it['id']}/complete", json={"outcome": "success"})
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE ledger_entries SET amount_minor = 1"))
    db.rollback()
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("DELETE FROM ledger_entries"))
    db.rollback()


def test_provider_catalog_states_are_honest(owner_api):
    data = owner_api.get("/api/admin/payments/methods").json()
    states = {m["key"]: m["state"] for m in data["items"]}
    assert states["cod"] == "built_in" and states["sandbox"] == "simulation"
    assert states["bkash"] == states["sslcommerz"] == "adapter_draft_unverified"
    for k in ("nagad", "aamarpay", "shurjopay", "uddoktapay", "stripe", "paypal"):
        assert states[k] == "catalog_only"
    assert data["external_network"] is False
    r = owner_api.patch("/api/admin/payments/methods/bkash", json={"enabled": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "cannot_enable"
    assert owner_api.patch("/api/admin/payments/methods/nagad", json={"enabled": True}).status_code == 409


def test_credentials_sealed_masked_and_verify_reports_real_reason(owner_api, db, store):
    from app.modules.platform.models import SecretRecord

    r = owner_api.put("/api/admin/payments/methods/bkash/credentials",
                      json={"values": {"app_key": "appkey-123456789", "app_secret": "very-secret-value-1"}})
    assert r.status_code == 200 and "very-secret" not in r.text
    assert r.json()["credentials"]["app_secret"].startswith("••••")
    rec = db.execute(select(SecretRecord).where(SecretRecord.ref == "payments.bkash.app_secret")).scalar_one()
    assert "very-secret" not in rec.sealed
    v = owner_api.post("/api/admin/payments/methods/bkash/verify").json()
    assert v["ok"] is False and "username" in v["message"]  # missing fields named
    owner_api.put("/api/admin/payments/methods/bkash/credentials",
                  json={"values": {"username": "merchant", "password": "pw-123456789"}})
    v = owner_api.post("/api/admin/payments/methods/bkash/verify").json()
    assert v["ok"] is False and v["state"] == "adapter_draft_unverified"
    assert "external network access is disabled" in v["message"]
    v = owner_api.post("/api/admin/payments/methods/nagad/verify").json()
    assert v["ok"] is False and v["message"].startswith("Not implemented")
    assert owner_api.put("/api/admin/payments/methods/bkash/credentials",
                         json={"values": {"bogus": "x"}}).status_code == 400
    listing = owner_api.get("/api/admin/payments/methods").text
    assert "very-secret" not in listing and "pw-123456789" not in listing


def test_payment_admin_permissions(new_api, make_staff, store):
    fin = staff_api(new_api, make_staff, "finance")
    assert fin.get("/api/admin/payments/methods").status_code == 200
    assert fin.patch("/api/admin/payments/methods/cod", json={"fee_minor": 100}).status_code == 403
    sup = staff_api(new_api, make_staff, "support_agent")
    assert sup.get("/api/admin/payments/ledger").status_code == 403


def test_bank_transfer_manual_verification(api, owner_api, db, store):
    r = owner_api.patch("/api/admin/payments/methods/bank_transfer", json={"enabled": True})
    assert r.json()["error"]["code"] == "instructions_required"
    r = owner_api.patch("/api/admin/payments/methods/bank_transfer", json={
        "enabled": True, "settings": {"instructions": "Send to City Bank 123", "bank_name": "City Bank"}})
    assert r.status_code == 200 and r.json()["config"]["enabled"]
    res, v = buy(api, db, store, method="bank_transfer")
    o = res["order"]
    assert o["status"] == "awaiting_payment" and res["next"] == {"type": "done"}
    view = api.get(f"/api/store/orders/{o['number']}?token={o['access_token']}").json()
    assert view["payment"]["instructions"]["instructions"] == "Send to City Bank 123"
    api.post(f"/api/store/orders/{o['number']}/bank-reference?token={o['access_token']}",
             json={"reference": "CB-TRX-778"})
    it = intents(owner_api, o["number"])[0]
    assert it["status"] == "awaiting_verification" and it["customer_reference"] == "CB-TRX-778"
    bad = owner_api.post(f"/api/admin/orders/{o['number']}/payments/{it['id']}/verify-transfer",
                         json={"reference": "CB-TRX-778", "amount_minor": 1})
    assert bad.status_code == 409
    ok = owner_api.post(f"/api/admin/orders/{o['number']}/payments/{it['id']}/verify-transfer",
                        json={"reference": "CB-TRX-778", "amount_minor": o["total_minor"]}).json()
    assert ok["status"] == "confirmed" and ok["payment_status"] == "paid"
    led = owner_api.get("/api/admin/payments/ledger").json()["items"]
    assert led[0]["kind"] == "bank_transfer_received" and led[0]["mode"] == "live"


def test_checkout_fee_and_cod_rules(api, owner_api, db, store):
    owner_api.patch("/api/admin/payments/methods/cod", json={"fee_minor": 2000})
    zones = owner_api.get("/api/admin/shipping/zones").json()["items"]
    outside = next(z for z in zones if z["name"] == "Outside Dhaka")["rates"][0]
    owner_api.put(f"/api/admin/shipping/rates/{outside['id']}", json={**{k: outside[k] for k in (
        "name", "kind", "amount_minor", "base_weight_grams", "per_kg_minor", "free_over_minor", "min_order_minor",
        "eta_label", "active", "position")}, "cod_allowed": True, "cod_fee_minor": 1500})
    v = product(db, store, price=100000)
    add(api, v.id)
    q = api.post("/api/store/checkout/quote", json={"address": {"district": "Cumilla"}, "payment_method": "cod"}).json()
    cod = next(m for m in q["payment_methods"] if m["key"] == "cod")
    assert cod["fee_minor"] == 3500 and q["totals"]["payment_fee_minor"] == 3500
    assert q["totals"]["total_minor"] == 100000 + 15000 + 3500
    owner_api.put(f"/api/admin/shipping/rates/{outside['id']}", json={**{k: outside[k] for k in (
        "name", "kind", "amount_minor", "base_weight_grams", "per_kg_minor", "free_over_minor", "min_order_minor",
        "eta_label", "active", "position")}, "cod_allowed": False, "cod_fee_minor": 0})
    q = api.post("/api/store/checkout/quote", json={"address": {"district": "Cumilla"}, "payment_method": "cod"}).json()
    cod = next(m for m in q["payment_methods"] if m["key"] == "cod")
    assert cod["available"] is False and "isn't offered" in cod["unavailable_reason"]


def test_vat_exclusive_added_to_total(api, owner_api, db, store):
    owner_api.patch("/api/settings", json={"changes": {"tax.vat_rate_bps": 1500, "tax.prices_include_tax": False}})
    v = product(db, store, price=100000)
    add(api, v.id)
    res, _ = checkout(api)
    assert res["order"]["total_minor"] == 100000 + 15000 + 8000


# ---------------------------------------------------------------- adapter drafts: request-shape contract tests
@pytest.fixture
def network(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "external_network", True)


def _ctx(db, store, key, creds, mode="sandbox"):
    from app.modules.payments.providers.base import ProviderContext

    ctx = ProviderContext(db=db, store_id=store.id, key=key, mode=mode)
    ctx.credentials = lambda: creds  # type: ignore[method-assign]
    return ctx


class _Intent:
    def __init__(self, amount=123450, ref=None, txn=None):
        import uuid

        self.id = uuid.UUID("12345678123456781234567812345678")
        self.amount_minor, self.currency = amount, "BDT"
        self.provider_ref, self.provider_txn_id, self.redirect_url = ref, txn, None
        self.captured_minor, self.refunded_minor = amount, 0


class _Order:
    number, name, phone, email = "VT-100123", "Rahim", "01711234567", "r@example.com"
    shipping_address = {"line1": "Road 1", "district": "Dhaka", "postal_code": "1205"}


@respx.mock
def test_bkash_adapter_request_shapes(db, store, network):
    from app.modules.payments.providers.bkash import Bkash

    base = "https://tokenized.sandbox.bka.sh/v1.2.0-beta/tokenized/checkout"
    grant = respx.post(f"{base}/token/grant").mock(return_value=httpx.Response(200, json={"id_token": "tok"}))
    create = respx.post(f"{base}/create").mock(return_value=httpx.Response(200, json={
        "statusCode": "0000", "paymentID": "PAY1", "bkashURL": "https://sandbox.payment.bkash.com/x"}))
    execute = respx.post(f"{base}/execute").mock(return_value=httpx.Response(200, json={
        "statusCode": "0000", "paymentID": "PAY1", "trxID": "TRX9", "transactionStatus": "Completed",
        "amount": "1234.50", "currency": "BDT", "merchantInvoiceNumber": "12345678123456781234567812345678"}))
    refund = respx.post(f"{base}/payment/refund").mock(return_value=httpx.Response(200, json={
        "transactionStatus": "Completed", "refundTrxID": "RF1"}))
    creds = {"app_key": "k", "app_secret": "s", "username": "u", "password": "p"}
    b, ctx, it = Bkash(), _ctx(db, store, "bkash", creds), _Intent()
    nxt = b.create_intent(ctx, it, _Order())
    assert nxt["url"].startswith("https://sandbox.payment.bkash.com") and it.provider_ref == "PAY1"
    g = grant.calls[0].request
    assert g.headers["username"] == "u" and json.loads(g.content) == {"app_key": "k", "app_secret": "s"}
    c = create.calls[0].request
    body = json.loads(c.content)
    assert c.headers["authorization"] == "tok" and c.headers["x-app-key"] == "k"
    assert body["mode"] == "0011" and body["amount"] == "1234.50" and body["intent"] == "sale"
    assert body["callbackURL"].endswith(f"/api/payments/return/bkash/{it.id}")
    ev = b.handle_return(ctx, it, {"paymentID": "PAY1", "status": "success"})
    assert json.loads(execute.calls[0].request.content) == {"paymentID": "PAY1"}
    assert ev.type == "payment.succeeded" and ev.amount_minor == 123450 and ev.provider_txn_id == "TRX9"
    with pytest.raises(Exception, match="does not belong"):
        b.handle_return(ctx, it, {"paymentID": "OTHER", "status": "success"})
    it.provider_txn_id = "TRX9"
    res = b.refund(ctx, it, 50000, "refund-key-1")
    rb = json.loads(refund.calls[0].request.content)
    assert rb["paymentID"] == "PAY1" and rb["trxID"] == "TRX9" and rb["amount"] == "500.00"
    assert res.status == "succeeded" and res.provider_refund_id == "RF1"


@respx.mock
def test_sslcommerz_adapter_request_shapes(db, store, network):
    from app.modules.payments.providers.sslcommerz import SslCommerz

    sess = respx.post("https://sandbox.sslcommerz.com/gwprocess/v4/api.php").mock(return_value=httpx.Response(
        200, json={"status": "SUCCESS", "GatewayPageURL": "https://sandbox.sslcommerz.com/pay/x", "sessionkey": "S1"}))
    val = respx.get("https://sandbox.sslcommerz.com/validator/api/validationserverAPI.php").mock(
        return_value=httpx.Response(200, json={"status": "VALID", "tran_id": "12345678123456781234567812345678",
                                               "amount": "1234.50", "currency": "BDT", "bank_tran_id": "B1"}))
    s, ctx, it = SslCommerz(), _ctx(db, store, "sslcommerz", {"store_id": "st", "store_passwd": "pw"}), _Intent()
    s.create_intent(ctx, it, _Order())
    form = dict(urllib.parse.parse_qsl(sess.calls[0].request.content.decode()))
    assert form["store_id"] == "st" and form["total_amount"] == "1234.50" and form["currency"] == "BDT"
    assert form["tran_id"] == it.id.hex and form["ipn_url"].endswith("/api/payments/webhooks/sslcommerz")
    ev = s.parse_webhook(ctx, {}, b"val_id=V1&status=VALID&amount=99999")  # IPN body amount is ignored
    q = dict(val.calls[0].request.url.params)
    assert q["val_id"] == "V1" and q["store_passwd"] == "pw" and q["format"] == "json"
    assert ev.type == "payment.succeeded" and ev.amount_minor == 123450 and ev.intent_id == it.id


def test_adapters_refuse_network_when_disabled(db, store):
    from app.modules.payments.providers.base import ProviderUnavailable
    from app.modules.payments.providers.bkash import Bkash

    with pytest.raises(ProviderUnavailable):
        Bkash().create_intent(_ctx(db, store, "bkash", {}), _Intent(), _Order())
