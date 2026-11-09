from __future__ import annotations

import io
import json
import zipfile

import httpx
import respx
from sqlalchemy import select

from tests.conftest import staff_api
from tests.test_ai_support import net  # noqa: F401

EXT = "https://ext.example.com"


def remote_manifest(**over) -> dict:
    m = {
        "schema": 1, "id": "acme.stock-sync", "name": "Acme stock sync", "version": "1.0.0", "publisher": "Acme",
        "description": "Remote test extension", "hostApi": ">=1.0 <2.0", "permissions": ["inventory.read"],
        "allowedHosts": ["ext.example.com"],
        "settings": {"type": "object", "properties": {"region": {"type": "string", "enum": ["dhaka", "ctg"],
                                                                 "default": "dhaka"}}},
        "secrets": [{"key": "token", "label": "Acme API token", "required": True}],
        "extensions": {"ai_tools": [{"name": "warehouse_forecast", "description": "Forecast stock-outs remotely",
                                     "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}},
                                     "permission": "inventory.read", "side_effect": "read",
                                     "handler": {"type": "remote", "url": f"{EXT}/tool"}}]},
        "hooks": {"health": f"{EXT}/health"},
    }
    m.update(over)
    return m


def upload(api, manifest: dict, name="manifest.json"):
    return api.post("/api/plugins/packages/upload", files={"file": (name, json.dumps(manifest).encode(),
                                                                    "application/json")})


def install_catalog(api, cid, perms):
    pkg = api.post("/api/plugins/packages", json={"catalog_id": cid})
    assert pkg.status_code == 200, pkg.text
    return api.post(f"/api/plugins/packages/{pkg.json()['package_id']}/install", json={"granted_permissions": perms})


def test_catalog_install_consent_and_free_shipping_component(owner_api, api):
    cat = owner_api.get("/api/plugins/catalog").json()["items"]
    assert {c["id"] for c in cat} == {"voltora.free-shipping-bar", "voltora.order-notify",
                                      "voltora.low-stock-assistant"}
    assert all(c["compatible"] for c in cat)
    pkg = owner_api.post("/api/plugins/packages", json={"catalog_id": "voltora.order-notify"}).json()
    assert pkg["permissions"][0]["key"] == "orders.read" and pkg["can_install"]
    r = owner_api.post(f"/api/plugins/packages/{pkg['package_id']}/install", json={"granted_permissions": []})
    assert r.status_code == 400 and r.json()["error"]["code"] == "consent_required"
    r = install_catalog(owner_api, "voltora.free-shipping-bar", [])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "disabled" and body["sandbox"]["status"] == "passed" and body["signing_secret"]
    assert api.get("/api/store/plugin-components").json() == {"components": [], "widgets": []}
    r = owner_api.put("/api/plugins/installed/voltora.free-shipping-bar/settings", json={"values": {
        "threshold_minor": -5}})
    assert r.status_code == 422
    owner_api.put("/api/plugins/installed/voltora.free-shipping-bar/settings", json={"values": {
        "threshold_minor": 250000}})
    r = owner_api.post("/api/plugins/installed/voltora.free-shipping-bar/enable")
    assert r.status_code == 200 and r.json()["status"] == "enabled", r.text
    comp = api.get("/api/store/plugin-components").json()
    assert comp["components"][0]["type"] == "plugin.voltora.free-shipping-bar.progress"
    assert comp["widgets"][0]["slot"] == "cart.summary" and comp["widgets"][0]["props"]["threshold_minor"] == 250000
    health = owner_api.post("/api/plugins/installed/voltora.free-shipping-bar/health").json()
    assert health["status"] == "healthy"
    caps = owner_api.get("/api/system/capabilities").json()["plugins"]
    assert caps[0]["id"] == "voltora.free-shipping-bar" and caps[0]["status"] == "enabled"
    owner_api.post("/api/plugins/installed/voltora.free-shipping-bar/disable")
    assert api.get("/api/store/plugin-components").json()["components"] == []


def test_incompatible_dependency_and_escalation_failures(owner_api, new_api, make_staff):
    r = upload(owner_api, remote_manifest(id="acme.future", hostApi=">=2.0.0"))
    assert r.status_code == 200 and r.json()["can_install"] is False
    r2 = owner_api.post(f"/api/plugins/packages/{r.json()['package_id']}/install",
                        json={"granted_permissions": ["inventory.read"]})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "incompatible"
    r = upload(owner_api, remote_manifest(id="acme.addon", dependencies={"acme.stock-sync": "^1.0.0"}))
    r2 = owner_api.post(f"/api/plugins/packages/{r.json()['package_id']}/install",
                        json={"granted_permissions": ["inventory.read"]})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "dependency_missing"
    dev = staff_api(new_api, make_staff, "developer")  # plugins.manage but not orders.read
    pkg = dev.post("/api/plugins/packages", json={"catalog_id": "voltora.order-notify"}).json()
    assert pkg["can_install"] is False
    r = dev.post(f"/api/plugins/packages/{pkg['package_id']}/install", json={"granted_permissions": ["orders.read"]})
    assert r.status_code == 403
    viewer = staff_api(new_api, make_staff, "auditor")
    assert viewer.get("/api/plugins/catalog").status_code == 200
    assert viewer.post("/api/plugins/packages", json={"catalog_id": "voltora.order-notify"}).status_code == 403


def test_invalid_manifests_rejected(owner_api):
    cases = [
        remote_manifest(permissions=["*"]),
        remote_manifest(permissions=["inventory.read", "made.up"]),
        remote_manifest(hooks={"health": "http://ext.example.com/health"}),
        remote_manifest(hooks={"health": "https://evil.example.org/health"}),
        remote_manifest(migrations=[{"sql": "drop table users"}]),
        remote_manifest(id="NotValid"),
        remote_manifest(settings={"type": "object", "properties": {"x": {"type": "string", "$ref": "#/x"}}}),
        {**remote_manifest(), "integrity": "sha256-" + "0" * 64},
        {**remote_manifest(), "unknown_field": True},
    ]
    for m in cases:
        r = upload(owner_api, m)
        assert r.status_code == 422, (m, r.text)
    from app.modules.plugins.manifest import integrity_of

    good = remote_manifest()
    good["integrity"] = integrity_of(good)
    assert upload(owner_api, good).status_code == 200


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, data in entries.items():
            z.writestr(n, data)
    return buf.getvalue()


def test_zip_upload_safe(owner_api):
    bad = _zip({"../manifest.json": json.dumps(remote_manifest()).encode()})
    r = owner_api.post("/api/plugins/packages/upload", files={"file": ("x.zip", bad, "application/zip")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_zip"
    r = owner_api.post("/api/plugins/packages/upload", files={"file": ("x.zip", _zip({"readme.txt": b"hi"}),
                                                                       "application/zip")})
    assert r.status_code == 422
    ok = _zip({"acme/manifest.json": json.dumps(remote_manifest()).encode(), "acme/README.md": b"# Acme docs"})
    r = owner_api.post("/api/plugins/packages/upload", files={"file": ("acme.zip", ok, "application/zip")})
    assert r.status_code == 200 and r.json()["readme"] == "# Acme docs" and len(r.json()["sha256"]) == 64
    r = owner_api.post("/api/plugins/packages/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400


def test_remote_extension_sandbox_signing_tool_and_uninstall_journey_17(owner_api, db, net, monkeypatch):
    from app.modules.webhooks.outbound import verify_signature

    pkg = upload(owner_api, remote_manifest()).json()
    assert pkg["warnings"] and pkg["allowed_hosts"] == ["ext.example.com"]
    seen = []

    def echo(request):
        body = json.loads(request.content)
        seen.append((request, body))
        if body.get("type") == "voltora.ping":
            return httpx.Response(200, json={"challenge": body["challenge"]})
        if body.get("type") == "voltora.ai_tool":
            return httpx.Response(200, json={"stockouts": [{"sku": "A", "days": 3}]})
        return httpx.Response(200, json={"ok": True})

    with respx.mock() as m:
        m.post(url__startswith=EXT).mock(side_effect=echo)
        r = owner_api.post(f"/api/plugins/packages/{pkg['package_id']}/install",
                           json={"granted_permissions": ["inventory.read"]})
        assert r.status_code == 200, r.text
        inst = r.json()
        secret = inst["signing_secret"]
        assert inst["sandbox"]["status"] == "passed", inst["sandbox"]
        req, _ = seen[0]
        assert verify_signature(secret, req.headers["X-Voltora-Signature"], req.content)
        # required secret missing → cannot enable
        r = owner_api.post("/api/plugins/installed/acme.stock-sync/enable")
        assert r.status_code == 409 and "Acme API token" in r.text
        owner_api.put("/api/plugins/installed/acme.stock-sync/secrets", json={"values": {"token": "acme-TOKEN-999"}})
        detail = owner_api.get("/api/plugins/installed/acme.stock-sync").json()
        assert detail["secrets"][0]["configured"] and "acme-TOKEN-999" not in json.dumps(detail)
        assert owner_api.post("/api/plugins/installed/acme.stock-sync/enable").json()["status"] == "enabled"
        tools = {t["name"]: t for t in owner_api.get("/api/ai/tools").json()["items"]}
        name = "x_acme_stock_sync_warehouse_forecast"
        assert tools[name]["source"] == "plugin:acme.stock-sync"
        from app.core.services import Actor
        from app.modules.ai.models import AiAgent
        from app.modules.ai.tools import ToolContext, invoke

        agent = AiAgent(allowed_tools=[name])
        ctx = ToolContext(db=db, store_id=_store_id(db), permissions=frozenset({"*"}),
                          actor=Actor("ai"))
        res = invoke(ctx, agent, name, {"days": 7})
        assert res["status"] == "ok" and res["result"]["data"]["stockouts"][0]["sku"] == "A"
        assert owner_api.post("/api/plugins/installed/acme.stock-sync/health").json()["status"] == "healthy"
    # uninstall keeping data, then reinstall restores settings; purge removes secrets
    owner_api.put("/api/plugins/installed/acme.stock-sync/settings", json={"values": {"region": "ctg"}})
    assert owner_api.delete("/api/plugins/installed/acme.stock-sync").status_code == 200
    assert owner_api.get("/api/plugins/installed").json()["items"] == []
    assert name not in {t["name"] for t in owner_api.get("/api/ai/tools").json()["items"]}
    pkg = upload(owner_api, remote_manifest()).json()
    assert pkg["current"]["status"] == "uninstalled"
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "external_network", False)  # sandbox ping is then skipped, not sent
    r = owner_api.post(f"/api/plugins/packages/{pkg['package_id']}/install",
                       json={"granted_permissions": ["inventory.read"]})
    assert r.json()["settings"]["region"] == "ctg" and r.json()["sandbox"]["status"] == "incomplete"
    assert owner_api.delete("/api/plugins/installed/acme.stock-sync?purge=true").status_code == 200
    from app.modules.platform.models import SecretRecord

    assert db.execute(select(SecretRecord).where(SecretRecord.owner_ref == "acme.stock-sync")).first() is None


def _store_id(db):
    from app.modules.platform.models import Store

    return db.execute(select(Store.id)).scalar()


def test_sandbox_failure_blocks_enable(owner_api, net):
    pkg = upload(owner_api, remote_manifest(secrets=[])).json()
    with respx.mock() as m:
        m.post(url__startswith=EXT).respond(200, json={"challenge": "wrong"})
        inst = owner_api.post(f"/api/plugins/packages/{pkg['package_id']}/install",
                              json={"granted_permissions": ["inventory.read"]}).json()
    assert inst["sandbox"]["status"] == "failed"
    r = owner_api.post("/api/plugins/installed/acme.stock-sync/enable")
    assert r.status_code == 409 and "sandbox" in r.text


def test_update_rollback_and_dependents(owner_api, net):
    base = remote_manifest(id="acme.base", secrets=[], extensions={}, hooks={}, allowedHosts=[], permissions=[])
    pkg = upload(owner_api, base).json()
    owner_api.post(f"/api/plugins/packages/{pkg['package_id']}/install", json={"granted_permissions": []})
    assert owner_api.post("/api/plugins/installed/acme.base/enable").status_code == 200
    child = remote_manifest(id="acme.child", secrets=[], extensions={}, hooks={}, allowedHosts=[], permissions=[],
                            dependencies={"acme.base": "^1.0.0"})
    pkg = upload(owner_api, child).json()
    assert pkg["can_install"]
    owner_api.post(f"/api/plugins/packages/{pkg['package_id']}/install", json={"granted_permissions": []})
    owner_api.post("/api/plugins/installed/acme.child/enable")
    r = owner_api.post("/api/plugins/installed/acme.base/disable")
    assert r.status_code == 409 and r.json()["error"]["code"] == "has_dependents"
    assert owner_api.delete("/api/plugins/installed/acme.base").status_code == 409
    v2 = {**base, "version": "1.1.0", "settings": {"type": "object", "properties": {
        "region": {"type": "string", "default": "dhaka"}, "mode": {"type": "string", "default": "fast"}}}}
    pkg = upload(owner_api, v2).json()
    r = owner_api.post("/api/plugins/installed/acme.base/update", json={"package_id": pkg["package_id"],
                                                                       "granted_permissions": []})
    assert r.status_code == 200 and r.json()["version"] == "1.1.0" and r.json()["settings"]["mode"] == "fast"
    assert r.json()["status"] == "enabled"
    r = owner_api.post("/api/plugins/installed/acme.base/rollback")
    assert r.status_code == 200 and r.json()["version"] == "1.0.0" and "mode" not in r.json()["settings"]
    assert owner_api.post("/api/plugins/installed/acme.base/rollback").status_code == 409


def test_low_stock_alias_tool(owner_api, db):
    assert install_catalog(owner_api, "voltora.low-stock-assistant", ["inventory.read"]).status_code == 200
    owner_api.post("/api/plugins/installed/voltora.low-stock-assistant/enable")
    tools = {t["name"]: t for t in owner_api.get("/api/ai/tools").json()["items"]}
    t = tools["x_voltora_low_stock_assistant_low_stock_digest"]
    assert t["side_effect"] == "read" and t["permission"] == "inventory.read"
    from app.core.services import Actor
    from app.modules.ai.models import AiAgent
    from app.modules.ai.tools import ToolContext, invoke

    ctx = ToolContext(db=db, store_id=_store_id(db), permissions=frozenset({"catalog.read"}), actor=Actor("ai"))
    agent = AiAgent(allowed_tools=[t["name"]])
    assert invoke(ctx, agent, t["name"], {})["status"] == "denied"  # user lacks inventory.read
    ctx.permissions = frozenset({"inventory.read"})
    assert invoke(ctx, agent, t["name"], {"limit": 5})["status"] == "ok"


def test_order_notify_slack_delivery_without_pii(owner_api, db, store, net):
    from app.core.services import emit, event_type, process_outbox
    from app.db import session_factory
    from app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint
    from app.modules.webhooks.service import deliver_due

    event_type("order.placed", "An order was placed")
    assert install_catalog(owner_api, "voltora.order-notify", ["orders.read"]).status_code == 200
    r = owner_api.post("/api/plugins/installed/voltora.order-notify/enable")
    assert r.status_code == 409 and "slack_webhook_url" in r.text
    hook = "https://hooks.slack.com/services/T000/B000/XXXSECRET"
    owner_api.put("/api/plugins/installed/voltora.order-notify/secrets", json={"values": {"slack_webhook_url": hook}})
    assert owner_api.post("/api/plugins/installed/voltora.order-notify/enable").status_code == 200
    eps = db.execute(select(WebhookEndpoint).where(WebhookEndpoint.plugin_id == "voltora.order-notify")).scalars().all()
    assert {e.name: e.enabled for e in eps} == {"voltora.order-notify:slack": True,
                                                "voltora.order-notify:telegram": False}
    assert all(e.url is None for e in eps)  # the secret URL is never stored on the endpoint
    emit(db, store.id, "order.placed", {"number": "VT-1001", "total_minor": 250000, "currency": "BDT",
                                        "email": "buyer@example.com", "phone": "01700000000"})
    db.commit()
    process_outbox(session_factory())
    with respx.mock() as m:
        route = m.post(hook).respond(200, text="ok")
        with session_factory()() as s:
            deliver_due(s)
            s.commit()
    sent = json.loads(route.calls[0].request.content)
    assert "VT-1001" in sent["text"] and "buyer@example.com" not in sent["text"] and "0170" not in sent["text"]
    d = db.execute(select(WebhookDelivery)).scalar_one()
    db.refresh(d)
    assert d.status == "succeeded" and "XXXSECRET" not in json.dumps(owner_api.get(
        f"/api/webhooks/deliveries/{d.id}").json())
