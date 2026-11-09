from __future__ import annotations

from pathlib import Path

from sqlalchemy import text


def test_installer_requires_token_then_closes(api, db):
    from app.modules.platform.install import bootstrap_path, ensure_bootstrap_token

    raw = ensure_bootstrap_token(db)
    assert raw and Path(bootstrap_path()).read_text().strip() == raw
    assert api.get("/api/install/status").json()["installed"] is False
    assert api.post("/api/install/verify").status_code == 401
    assert api.post("/api/install/verify", headers={"X-Bootstrap-Token": "wrong"}).status_code == 401
    h = {"X-Bootstrap-Token": raw}
    assert api.get("/api/install/checks", headers=h).status_code == 200
    r = api.post("/api/install/owner", json={"email": "o@example.com", "name": "O", "password": "owner-pass-123"},
                 headers=h)
    assert r.status_code == 400  # store first
    assert api.post("/api/install/store", json={"name": "Dhaka Goods"}, headers=h).status_code == 200
    assert api.post("/api/install/owner", json={"email": "o@example.com", "name": "O", "password": "owner-pass-123"},
                    headers=h).status_code == 200
    # resume: progress is persisted server-side
    prog = api.post("/api/install/verify", headers=h).json()["progress"]
    assert prog["store"]["done"] and prog["owner"]["done"]
    assert api.post("/api/install/complete", headers=h).status_code == 200
    assert api.post("/api/install/verify", headers=h).status_code == 410
    assert not Path(bootstrap_path()).exists()
    assert api.get("/api/install/status").json()["installed"] is True
    r = api.post("/api/auth/login", json={"email": "o@example.com", "password": "owner-pass-123"})
    assert r.status_code == 200
    assert "*" in api.get("/api/auth/me").json()["permissions"]


def test_settings_validation_revisions_and_revert(owner_api):
    r = owner_api.patch("/api/settings", json={"changes": {"email.smtp_port": "abc"}})
    assert r.status_code == 400
    r = owner_api.patch("/api/settings", json={"changes": {"nope.key": 1}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_setting"
    owner_api.patch("/api/settings", json={"changes": {"store.tagline": "One"}})
    owner_api.patch("/api/settings", json={"changes": {"store.tagline": "Two"}})
    revs = owner_api.get("/api/settings/revisions").json()["items"]
    assert revs[0]["changes"]["store.tagline"] == {"from": "One", "to": "Two"}
    owner_api.post(f"/api/settings/revisions/{revs[0]['id']}/revert")
    cfg = owner_api.get("/api/storefront/config").json()
    assert cfg["settings"]["store.tagline"] == "One"


def test_audit_chain_detects_tampering(owner_api, db):
    owner_api.patch("/api/settings", json={"changes": {"store.tagline": "A"}})
    owner_api.patch("/api/settings", json={"changes": {"store.tagline": "B"}})
    assert owner_api.get("/api/audit/verify").json()["ok"] is True
    db.execute(text("UPDATE audit_events SET data = '{\"keys\": [\"forged\"]}' WHERE seq = (SELECT min(seq) FROM audit_events WHERE action='settings.update')"))
    db.commit()
    assert owner_api.get("/api/audit/verify").json()["ok"] is False


def test_outbox_retry_and_dead_letter(db, store):
    from app.core import services
    from app.db import session_factory
    from app.modules.platform.models import OutboxEvent

    calls = {"n": 0}

    @services.on("test.flaky", name="flaky")
    def flaky(_db, ev):
        calls["n"] += 1
        raise RuntimeError("boom")

    services.emit(db, store.id, "test.flaky", {"x": 1})
    db.commit()
    for _ in range(services.MAX_ATTEMPTS):
        db.execute(text("UPDATE outbox_events SET next_attempt_at = now() - interval '1 second'"))
        db.commit()
        services.process_outbox(session_factory())
    ev = db.query(OutboxEvent).filter_by(type="test.flaky").one()
    db.refresh(ev)
    assert ev.status == "dead" and calls["n"] == services.MAX_ATTEMPTS and "boom" in ev.last_error
    services._handlers["test.flaky"].clear()


def test_secrets_never_returned(owner_api):
    r = owner_api.put("/api/messaging/smtp-password", json={"password": "super-secret-smtp-pw"})
    assert r.json()["hint"].endswith("p-pw") and "super" not in r.text
    assert "super-secret" not in owner_api.get("/api/settings").text
    assert "super-secret" not in owner_api.get("/api/audit").text


def test_email_without_transport_is_held_not_sent(owner_api):
    r = owner_api.post("/api/messaging/test-email", json={"to": "x@example.com"})
    assert r.json()["status"] == "held" and "not configured" in r.json()["error"]


def test_capability_graph_reflects_permissions(owner_api, new_api, make_staff):
    from tests.conftest import staff_api

    g = owner_api.get("/api/system/capabilities").json()
    assert "platform" in g["modules"] and any(r["path"] == "/api/settings" for r in g["routes"])
    sup = staff_api(new_api, make_staff, "support_agent")
    g2 = sup.get("/api/system/capabilities").json()
    route = next(r for r in g2["routes"] if r["path"] == "/api/staff/roles" and "POST" in r["methods"])
    assert route["allowed_for_you"] is False
