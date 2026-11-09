from __future__ import annotations

import datetime as dt
import json

import httpx
import respx
from sqlalchemy import select

from tests.conftest import staff_api
from tests.test_ai_support import net  # noqa: F401

URL = "https://hooks.example.com/voltora"


def _endpoint(api, events=("customer.registered",)):
    r = api.post("/api/webhooks/endpoints", json={"name": "CRM", "url": URL, "events": list(events)})
    assert r.status_code == 200, r.text
    return r.json()


def _deliver_all():
    from app.db import session_factory
    from app.modules.webhooks.service import deliver_due

    with session_factory()() as s:
        out = deliver_due(s)
        s.commit()
    return out


def test_endpoint_validation_and_permissions(owner_api, new_api, make_staff, net):
    for url in ("http://hooks.example.com/x", "https://internal.example.test/x", "https://169.254.169.254/x",
                "https://metadata.google.internal/x"):
        r = owner_api.post("/api/webhooks/endpoints", json={"name": "Bad", "url": url,
                                                            "events": ["customer.registered"]})
        assert r.status_code == 400 and r.json()["error"]["code"] == "unsafe_url", url
    r = owner_api.post("/api/webhooks/endpoints", json={"name": "Bad", "url": URL, "events": ["nope.event"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_event"
    ep = _endpoint(owner_api)
    assert ep["secret"].startswith("whsec_")
    listed = owner_api.get("/api/webhooks/endpoints").json()["items"][0]
    assert "secret" not in listed and listed["secret_hint"] == ep["secret"][-4:]
    mgr = staff_api(new_api, make_staff, "store_manager")
    assert mgr.get("/api/webhooks/endpoints").status_code == 403
    types = owner_api.get("/api/webhooks/event-types").json()["items"]
    assert any(t["type"] == "customer.registered" for t in types)


def test_signed_delivery_retry_dead_letter_and_redelivery_idempotent(owner_api, api, db, store, net):
    from app.core.services import process_outbox
    from app.db import session_factory
    from app.modules.platform.models import OutboxEvent
    from app.modules.webhooks.models import WebhookDelivery
    from app.modules.webhooks.outbound import verify_signature
    from app.modules.webhooks.service import MAX_ATTEMPTS

    ep = _endpoint(owner_api)
    api.post("/api/auth/register", json={"email": "new@example.com", "password": "correct-horse-42", "name": "N"})
    process_outbox(session_factory())
    # outbox redelivery of the same event must not create a second delivery
    db.execute(OutboxEvent.__table__.update().values(status="pending", results={}))
    db.commit()
    process_outbox(session_factory())
    assert len(db.execute(select(WebhookDelivery)).scalars().all()) == 1
    with respx.mock() as m:
        route = m.post(URL).mock(side_effect=[httpx.Response(500, text="nope")])
        _deliver_all()
        req = route.calls[0].request
        assert verify_signature(ep["secret"], req.headers["X-Voltora-Signature"], req.content)
        body = json.loads(req.content)
        assert body["type"] == "customer.registered" and body["data"]["email"] == "new@example.com"
    d = db.execute(select(WebhookDelivery)).scalar_one()
    db.refresh(d)
    assert d.status == "failed" and d.attempts == 1 and d.next_attempt_at > d.created_at
    first_wait = d.next_attempt_at - d.created_at
    # exhaust retries: force due each time
    with respx.mock() as m:
        m.post(URL).respond(503)
        for _ in range(MAX_ATTEMPTS - 1):
            db.execute(WebhookDelivery.__table__.update().values(next_attempt_at=dt.datetime(2000, 1, 1,
                                                                                             tzinfo=dt.UTC)))
            db.commit()
            _deliver_all()
    db.refresh(d)
    assert d.status == "dead" and d.attempts == MAX_ATTEMPTS
    assert first_wait < dt.timedelta(seconds=40)
    # manual replay succeeds and is linked to the original
    with respx.mock() as m:
        m.post(URL).respond(204)
        r = owner_api.post(f"/api/webhooks/deliveries/{d.id}/replay")
    assert r.status_code == 200 and r.json()["status"] == "succeeded" and r.json()["replay_of"] == str(d.id)
    assert verify_signature("wrong", req.headers["X-Voltora-Signature"], req.content) is False


def test_ping_and_network_disabled_is_held(owner_api, net, monkeypatch):
    ep = _endpoint(owner_api)
    with respx.mock() as m:
        m.post(URL).respond(200, json={"ok": True})
        r = owner_api.post(f"/api/webhooks/endpoints/{ep['id']}/ping")
    assert r.status_code == 200 and r.json()["status"] == "succeeded" and r.json()["event_type"] == "voltora.ping"
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "external_network", False)
    r = owner_api.post(f"/api/webhooks/endpoints/{ep['id']}/ping").json()
    assert r["status"] == "held" and "disabled" in r["error"] and r["attempts"] == 0
    rot = owner_api.post(f"/api/webhooks/endpoints/{ep['id']}/rotate-secret").json()
    assert rot["secret"] != ep["secret"]
    assert owner_api.patch(f"/api/webhooks/endpoints/{ep['id']}", json={"enabled": False}).json()["enabled"] is False
    assert owner_api.delete(f"/api/webhooks/endpoints/{ep['id']}").status_code == 200
