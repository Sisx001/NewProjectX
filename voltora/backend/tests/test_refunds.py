"""Golden journey 7: partial/full refunds exactly once with a durable ledger, dual control, manual payouts."""
from __future__ import annotations

import threading

from sqlalchemy import func, select

from tests.conftest import staff_api
from tests.test_commerce_support import buy, intents, level, sandbox_account_id


def _paid_order(api, owner_api, db, store, qty=2, price=100000):
    from tests.test_commerce_support import product

    v = product(db, store, price=price, stock=5)
    res, _ = buy(api, db, store, variant=v, qty=qty, method="sandbox")
    it = intents(owner_api, res["order"]["number"])[-1]
    api.post(f"/api/payments/sandbox/{it['id']}/complete", json={"outcome": "success"})
    return res["order"], v


def _refund(client, number, key, amount, **kw):
    return client.post(f"/api/admin/orders/{number}/refunds", json={"idempotency_key": key, "amount_minor": amount,
                                                                    "reason": "test", **kw})


def _ledger(db, kind=None):
    from app.modules.payments.models import LedgerEntry

    db.expire_all()
    q = select(LedgerEntry)
    if kind:
        q = q.where(LedgerEntry.kind == kind)
    return db.execute(q.order_by(LedgerEntry.id)).scalars().all()


def test_partial_then_full_refund_exactly_once(api, owner_api, db, store, monkeypatch):
    from app.modules.payments.providers.builtin import SandboxGateway

    o, _ = _paid_order(api, owner_api, db, store)
    calls = []
    real = SandboxGateway.refund

    def counting(self, ctx, intent, amount, key):
        calls.append((amount, key))
        return real(self, ctx, intent, amount, key)

    monkeypatch.setattr(SandboxGateway, "refund", counting)
    r1 = _refund(owner_api, o["number"], "rf-partial-0001", 50000)
    assert r1.status_code == 200 and r1.json()["status"] == "succeeded"
    assert r1.json()["provider_refund_id"].startswith("sbx_rf_")
    again = _refund(owner_api, o["number"], "rf-partial-0001", 50000)  # double submit
    assert again.json()["id"] == r1.json()["id"]
    assert len(calls) == 1
    d = owner_api.get(f"/api/admin/orders/{o['number']}").json()
    assert d["payment_status"] == "partially_refunded" and d["totals"]["refunded_minor"] == 50000
    rest = o["total_minor"] - 50000
    assert d["payment"]["refundable_minor"] == rest
    r2 = _refund(owner_api, o["number"], "rf-full-0002", rest)
    assert r2.json()["status"] == "succeeded"
    d = owner_api.get(f"/api/admin/orders/{o['number']}").json()
    assert d["payment_status"] == "refunded" and d["payment"]["refundable_minor"] == 0
    refunds = _ledger(db, "refund")
    assert [e.amount_minor for e in refunds] == [-50000, -rest]
    assert sum(e.amount_minor for e in _ledger(db)) == 0
    over = _refund(owner_api, o["number"], "rf-over-0003", 1)
    assert over.status_code == 409 and over.json()["error"]["code"] in ("over_refund", "nothing_to_refund")
    mismatch = _refund(owner_api, o["number"], "rf-partial-0001", 1)
    assert mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "idempotency_mismatch"


def test_over_refund_rejected(api, owner_api, db, store):
    o, _ = _paid_order(api, owner_api, db, store)
    r = _refund(owner_api, o["number"], "rf-too-much-01", o["total_minor"] + 1)
    assert r.status_code == 409 and r.json()["error"]["details"]["refundable_minor"] == o["total_minor"]


def test_concurrent_refunds_cannot_exceed_captured(api, owner_api, new_api, make_staff, db, store):
    from app.modules.payments.models import Refund

    o, _ = _paid_order(api, owner_api, db, store)
    clients = [owner_api] + [staff_api(new_api, make_staff, "finance") for _ in range(3)]
    amount = o["total_minor"] * 6 // 10
    barrier = threading.Barrier(len(clients))
    out = [None] * len(clients)

    def run(i):
        barrier.wait()
        out[i] = _refund(clients[i], o["number"], f"rf-race-{i:04d}-xx", amount)

    ts = [threading.Thread(target=run, args=(i,)) for i in range(len(clients))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r.status_code for r in out) == [200, 409, 409, 409], [r.text for r in out]
    db.expire_all()
    assert db.execute(select(func.count()).select_from(Refund).where(Refund.status == "succeeded")).scalar_one() == 1
    assert len(_ledger(db, "refund")) == 1


def test_same_key_concurrent_double_submit_single_refund(api, owner_api, db, store, monkeypatch):
    from app.modules.payments.models import Refund

    o, _ = _paid_order(api, owner_api, db, store)
    barrier = threading.Barrier(4)
    out = []

    def run():
        barrier.wait()
        out.append(_refund(owner_api, o["number"], "rf-same-key-9999", 10000))

    ts = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(r.status_code == 200 for r in out)
    assert len({r.json()["id"] for r in out}) == 1
    db.expire_all()
    assert db.execute(select(func.count()).select_from(Refund)).scalar_one() == 1
    assert len(_ledger(db, "refund")) == 1


def test_dual_control_above_threshold(api, owner_api, new_api, make_staff, db, store):
    owner_api.patch("/api/settings", json={"changes": {"refunds.approval_threshold_minor": 50000}})
    o, _ = _paid_order(api, owner_api, db, store)
    small = _refund(owner_api, o["number"], "rf-small-00001", 10000).json()
    assert small["status"] == "succeeded"
    big = _refund(owner_api, o["number"], "rf-big-0000001", 60000).json()
    assert big["status"] == "awaiting_approval"
    assert owner_api.get("/api/admin/home").json()["queues"]["refunds_awaiting_approval"] == 1
    # the reserved amount counts against what can still be refunded
    left = o["total_minor"] - 10000 - 60000
    assert owner_api.get(f"/api/admin/orders/{o['number']}").json()["payment"]["refundable_minor"] == left
    same = owner_api.post(f"/api/admin/refunds/{big['id']}/approve")
    assert same.status_code == 403 and same.json()["error"]["code"] == "second_approver_required"
    sup = staff_api(new_api, make_staff, "support_agent")
    assert sup.post(f"/api/admin/refunds/{big['id']}/approve").status_code == 403
    fin = staff_api(new_api, make_staff, "finance")
    ok = fin.post(f"/api/admin/refunds/{big['id']}/approve").json()
    assert ok["status"] == "succeeded" and ok["approved_by"] == fin.user.email
    assert fin.post(f"/api/admin/refunds/{big['id']}/approve").status_code == 409


def test_refund_permission_denied(api, owner_api, new_api, make_staff, db, store):
    o, _ = _paid_order(api, owner_api, db, store)
    for role in ("support_agent", "store_manager", "fulfillment"):
        c = staff_api(new_api, make_staff, role)
        assert _refund(c, o["number"], f"rf-deny-{role}", 100).status_code == 403


def test_cod_refund_is_manual_payout_with_restock(api, owner_api, db, store):
    res, v = buy(api, db, store, qty=2)
    n = res["order"]["number"]
    assert _refund(owner_api, n, "rf-cod-early-1", 100).json()["error"]["code"] == "nothing_to_refund"
    owner_api.post(f"/api/admin/orders/{n}/transition", json={"to": "confirmed"})
    acc = sandbox_account_id(owner_api)
    d = owner_api.post(f"/api/admin/orders/{n}/shipments", json={"account_id": acc}).json()
    sid = d["shipments"][0]["id"]
    for s in ("picked_up", "in_transit", "delivered"):
        owner_api.post(f"/api/admin/shipments/{sid}/simulate", json={"status": s})
    d = owner_api.get(f"/api/admin/orders/{n}").json()
    assert d["payment_status"] == "paid" and level(db, v.id) == (3, 0)
    line = d["lines"][0]
    r = _refund(owner_api, n, "rf-cod-0000001", 120000, lines=[{"line_id": line["id"], "quantity": 1,
                                                                "restock": True}])
    assert r.json()["error"]["code"] == "payout_method_required"
    r = _refund(owner_api, n, "rf-cod-0000002", 120000, method="bkash_send_money",
                lines=[{"line_id": line["id"], "quantity": 1, "restock": True}])
    assert r.json()["error"]["code"] == "payout_reference_required"
    r = _refund(owner_api, n, "rf-cod-0000003", 120000, method="bkash_send_money", reference="BK-SEND-1",
                lines=[{"line_id": line["id"], "quantity": 1, "restock": True}]).json()
    assert r["status"] == "succeeded" and r["restocked"] is True
    assert level(db, v.id) == (4, 0)
    e = _ledger(db, "manual_payout")[0]
    assert e.amount_minor == -120000 and e.reference == "BK-SEND-1" and e.method == "bkash_send_money"
    d = owner_api.get(f"/api/admin/orders/{n}").json()
    assert d["lines"][0]["returned_qty"] == 1 and d["lines"][0]["refunded_qty"] == 1
    r = _refund(owner_api, n, "rf-cod-0000004", 100, method="cash", reference="x",
                lines=[{"line_id": line["id"], "quantity": 1, "restock": True}, ])
    assert r.status_code == 200  # second unit: still shipped & not returned → restock allowed
    r = _refund(owner_api, n, "rf-cod-0000005", 100, method="cash", reference="x",
                lines=[{"line_id": line["id"], "quantity": 1, "restock": True}])
    assert r.json()["error"]["code"] == "line_over_refund"


def test_paid_order_cannot_be_cancelled_without_refund(api, owner_api, db, store):
    o, v = _paid_order(api, owner_api, db, store)
    r = owner_api.post(f"/api/admin/orders/{o['number']}/transition", json={"to": "cancelled", "note": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "refund_required"
    _refund(owner_api, o["number"], "rf-before-cancel", o["total_minor"])
    r = owner_api.post(f"/api/admin/orders/{o['number']}/transition", json={"to": "cancelled", "note": "x"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert level(db, v.id) == (5, 0)
