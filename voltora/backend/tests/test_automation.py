from __future__ import annotations

import respx
from sqlalchemy import func, select

from tests.conftest import PASSWORD, staff_api
from tests.test_ai_support import net  # noqa: F401


def _wf(api, **over):
    body = {"name": "Welcome", "trigger_event": "customer.registered", "enabled": True,
            "conditions": {"and": [{"contains": [{"var": "email"}, "@example.com"]},
                                   {"!=": [{"var": "email"}, "blocked@example.com"]}]},
            "actions": [{"type": "send_email", "to": "{{email}}", "subject": "Welcome!",
                         "body": "Hi {{email}}, thanks for joining."},
                        {"type": "notify", "title": "New customer {{email}}", "permission": "customers.read"}]}
    body.update(over)
    r = api.post("/api/automation/workflows", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _outbox():
    from app.core.services import process_outbox
    from app.db import session_factory

    process_outbox(session_factory())


def test_event_triggers_exactly_one_idempotent_action_journey_14(owner_api, api, db):
    from app.modules.automation.models import WorkflowRun
    from app.modules.messaging.models import OutboundMessage
    from app.modules.platform.models import Notification, OutboxEvent

    wf = _wf(owner_api)
    r = api.post("/api/auth/register", json={"email": "shopper@example.com", "password": PASSWORD, "name": "S"})
    assert r.status_code == 200
    _outbox()
    # simulate the outbox redelivering the same event (crash after handler, lost result, manual retry …)
    db.execute(OutboxEvent.__table__.update().values(status="pending", results={}))
    db.commit()
    _outbox()
    _outbox()
    runs = db.execute(select(WorkflowRun)).scalars().all()
    assert len(runs) == 1 and runs[0].status == "succeeded" and [s["status"] for s in runs[0].steps] == [
        "succeeded", "succeeded"]
    msgs = db.execute(select(OutboundMessage).where(OutboundMessage.to == "shopper@example.com")).scalars().all()
    assert len(msgs) == 1 and msgs[0].subject == "Welcome!" and "shopper@example.com" in msgs[0].body
    assert db.execute(select(func.count()).select_from(Notification).where(Notification.kind == "automation")
                      ).scalar() == 1
    listed = owner_api.get("/api/automation/runs").json()
    assert listed["total"] == 1 and listed["items"][0]["workflow_id"] == wf["id"]
    assert owner_api.get(f"/api/automation/runs/{runs[0].id}").json()["payload"]["email"] == "shopper@example.com"


def test_conditions_false_skips(owner_api, api, db):
    from app.modules.automation.models import WorkflowRun
    from app.modules.messaging.models import OutboundMessage

    _wf(owner_api, conditions={"==": [{"var": "email"}, "someone-else@example.com"]})
    api.post("/api/auth/register", json={"email": "x@example.com", "password": PASSWORD, "name": "X"})
    _outbox()
    run = db.execute(select(WorkflowRun)).scalar_one()
    assert run.status == "skipped" and run.conditions_met is False
    assert db.execute(select(OutboundMessage).where(OutboundMessage.to == "x@example.com")).first() is None


def test_failed_step_retry_resumes_without_repeating(owner_api, api, db, net):
    from app.modules.automation.models import WorkflowRun
    from app.modules.platform.models import Notification
    from app.modules.webhooks.models import WebhookDelivery

    ep = owner_api.post("/api/webhooks/endpoints", json={"name": "Ops", "url": "https://hooks.example.com/a",
                                                         "events": ["customer.registered"]}).json()
    owner_api.patch(f"/api/webhooks/endpoints/{ep['id']}", json={"enabled": False})
    _wf(owner_api, conditions=None, actions=[{"type": "notify", "title": "Signup {{email}}"},
                                             {"type": "call_webhook", "endpoint_id": ep["id"]}])
    api.post("/api/auth/register", json={"email": "r@example.com", "password": PASSWORD, "name": "R"})
    _outbox()
    run = db.execute(select(WorkflowRun)).scalar_one()
    assert run.status == "failed" and run.steps[0]["status"] == "succeeded" and run.steps[1]["status"] == "failed"
    assert run.next_retry_at is not None
    owner_api.patch(f"/api/webhooks/endpoints/{ep['id']}", json={"enabled": True})
    r = owner_api.post(f"/api/automation/runs/{run.id}/retry")
    assert r.status_code == 200 and r.json()["status"] == "succeeded", r.text
    assert db.execute(select(func.count()).select_from(Notification).where(Notification.kind == "automation")
                      ).scalar() == 1
    d = db.execute(select(WebhookDelivery).where(WebhookDelivery.source == "automation")).scalar_one()
    assert d.payload["event"]["email"] == "r@example.com"
    with respx.mock():
        pass


def test_validation_and_dry_run(owner_api, db):
    from app.modules.messaging.models import OutboundMessage

    bad = [
        {"trigger_event": "nope.event"},
        {"conditions": {"eval": ["1+1"]}},
        {"conditions": {"==": [{"var": "__import__('os')"}, 1]}},
        {"actions": []},
        {"actions": [{"type": "shell", "cmd": "rm -rf /"}]},
        {"actions": [{"type": "send_email", "to": "not-an-email", "subject": "a", "body": "b"}]},
        {"actions": [{"type": "notify", "title": "x"}] * 21},
        {"trigger_event": "automation.run_failed"},
    ]
    for over in bad:
        body = {"name": "Bad", "trigger_event": "customer.registered", "enabled": True,
                "actions": [{"type": "notify", "title": "x"}], **over}
        r = owner_api.post("/api/automation/workflows", json=body)
        assert r.status_code in (400, 422), (over, r.text)
    wf = _wf(owner_api)
    r = owner_api.post(f"/api/automation/workflows/{wf['id']}/test", json={"payload": {"email": "t@example.com"}})
    out = r.json()
    assert out["conditions_met"] is True and out["steps"][0]["preview"]["to"] == "t@example.com"
    assert db.execute(select(OutboundMessage)).first() is None
    r = owner_api.post(f"/api/automation/workflows/{wf['id']}/test", json={"payload": {"email": "t@other.org"}})
    assert r.json()["conditions_met"] is False
    r = owner_api.patch(f"/api/automation/workflows/{wf['id']}", json={"name": "Renamed"})
    assert r.json()["version"] == 1
    r = owner_api.patch(f"/api/automation/workflows/{wf['id']}", json={"conditions": None})
    assert r.json()["version"] == 2 and r.json()["conditions"] is None
    cat = owner_api.get("/api/automation/catalog").json()
    assert any(t["type"] == "customer.registered" for t in cat["triggers"]) and "contains" in cat["operators"]


def test_ai_draft_action_queues_task_and_reports_missing_provider(owner_api, api, db):
    from app.db import session_factory
    from app.modules.ai.models import AiTask
    from app.modules.ai.service import process_tasks

    agents = owner_api.get("/api/ai/agents").json()["items"]
    copilot = next(a for a in agents if a["role"] == "owner_copilot")
    _wf(owner_api, conditions=None, actions=[{"type": "ai_draft", "agent_id": copilot["id"],
                                              "prompt": "Draft a welcome note for {{email}}"}])
    api.post("/api/auth/register", json={"email": "ai@example.com", "password": PASSWORD, "name": "A"})
    _outbox()
    task = db.execute(select(AiTask)).scalar_one()
    assert task.prompt == "Draft a welcome note for ai@example.com" and task.status == "queued"
    with session_factory()() as s:
        process_tasks(s)
        s.commit()
    db.refresh(task)
    assert task.status == "blocked" and "No AI provider" in task.result


def test_permissions(owner_api, new_api, make_staff):
    mgr = staff_api(new_api, make_staff, "store_manager")
    assert mgr.get("/api/automation/workflows").status_code == 403
    dev = staff_api(new_api, make_staff, "developer")  # automation.manage, but not customers.write
    r = dev.post("/api/automation/workflows", json={
        "name": "Tagger", "trigger_event": "customer.registered",
        "actions": [{"type": "add_customer_tag", "customer_id_path": "user_id", "tag": "new"}]})
    assert r.status_code == 403
