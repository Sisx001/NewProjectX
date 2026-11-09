"""Workflow engine: event trigger → conditions → ordered actions, exactly-once per (workflow, event).

Each action runs in its own savepoint and its result is stored on the run in the same transaction, so a retried run
resumes after the last successful step and never repeats one. External calls are queued through the webhook outbox
instead of being made inline.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...core.errors import AppError
from ...core.rbac import PERMISSIONS
from ...core.services import _canon, on, periodic
from ...db import utcnow
from .logic import LogicError, evaluate, render, validate_logic
from .models import Workflow, WorkflowRun

MAX_ACTIONS = 20
MAX_ATTEMPTS = 3
ACTION_TYPES: dict[str, dict] = {
    "send_email": {"label": "Send email", "fields": {"to": "Recipient (email or {{path}})", "subject": "Subject",
                                                     "body": "Body (supports {{path}})"}},
    "notify": {"label": "In-app notification", "fields": {"title": "Title", "body": "Body",
                                                          "permission": "Only staff with permission (optional)",
                                                          "severity": "info | warning | danger"}},
    "add_customer_tag": {"label": "Tag customer", "fields": {"customer_id_path": "Payload path of the customer id",
                                                             "tag": "Tag"}},
    "call_webhook": {"label": "Call webhook", "fields": {"endpoint_id": "Developer webhook endpoint"}},
    "ai_draft": {"label": "Ask an AI assistant to draft", "fields": {"agent_id": "Assistant", "prompt":
                                                                     "Instructions (supports {{path}})"}},
}


class ActionError(Exception):
    pass


def _uuid(v: Any, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except ValueError as exc:
        raise AppError("invalid_action", f"{what} is not a valid id") from exc


def validate_definition(db: Session, store_id, trigger: str, conditions: Any, actions: list,
                        editor_perms: frozenset) -> None:
    from ...core.services import EVENT_TYPES

    if trigger not in EVENT_TYPES and not (trigger.endswith(".*") and any(
            t.startswith(trigger[:-1]) for t in EVENT_TYPES)):
        raise AppError("unknown_trigger", f"Unknown trigger event {trigger}")
    if trigger.startswith("automation."):
        raise AppError("loop_risk", "Automations cannot trigger on automation events")
    try:
        validate_logic(conditions or None)
    except LogicError as exc:
        raise AppError("invalid_conditions", str(exc), status=422) from exc
    if not actions:
        raise AppError("actions_required", "Add at least one action")
    if len(actions) > MAX_ACTIONS:
        raise AppError("too_many_actions", f"At most {MAX_ACTIONS} actions per workflow")
    def can(perm: str) -> bool:
        return "*" in editor_perms or perm in editor_perms

    for i, a in enumerate(actions):
        t = a.get("type") if isinstance(a, dict) else None
        if t not in ACTION_TYPES:
            raise AppError("invalid_action", f"Action {i + 1}: unknown type {t}")
        where = f"Action {i + 1} ({ACTION_TYPES[t]['label']})"
        if t == "send_email":
            if not a.get("to") or not a.get("subject") or not a.get("body"):
                raise AppError("invalid_action", f"{where}: recipient, subject and body are required")
            if "{{" not in a["to"] and "@" not in a["to"]:
                raise AppError("invalid_action", f"{where}: recipient must be an email or a {{{{path}}}}")
        elif t == "notify":
            if not a.get("title"):
                raise AppError("invalid_action", f"{where}: title is required")
            if a.get("permission") and a["permission"] not in PERMISSIONS:
                raise AppError("invalid_action", f"{where}: unknown permission")
            if a.get("severity", "info") not in ("info", "warning", "danger"):
                raise AppError("invalid_action", f"{where}: unknown severity")
        elif t == "add_customer_tag":
            if not can("customers.write"):
                raise AppError("forbidden", f"{where}: needs customers.write", status=403)
            if not a.get("customer_id_path") or not a.get("tag") or len(a["tag"]) > 40:
                raise AppError("invalid_action", f"{where}: customer id path and a tag (≤40 chars) are required")
        elif t == "call_webhook":
            from ..webhooks.models import WebhookEndpoint

            ep = db.get(WebhookEndpoint, _uuid(a.get("endpoint_id"), f"{where}: endpoint"))
            if ep is None or ep.store_id != store_id or ep.owner_kind != "developer":
                raise AppError("invalid_action", f"{where}: choose one of your webhook endpoints")
            if not can("developer.manage"):
                raise AppError("forbidden", f"{where}: needs developer.manage", status=403)
        elif t == "ai_draft":
            from ..ai.models import AiAgent

            ag = db.get(AiAgent, _uuid(a.get("agent_id"), f"{where}: assistant"))
            if ag is None or ag.store_id != store_id or ag.channel != "admin":
                raise AppError("invalid_action", f"{where}: choose an admin assistant")
            if not a.get("prompt"):
                raise AppError("invalid_action", f"{where}: instructions are required")
            if not can("ai.use"):
                raise AppError("forbidden", f"{where}: needs ai.use", status=403)


def preview_action(a: dict, payload: dict) -> dict:
    out = {"type": a["type"]}
    for k, v in a.items():
        if k != "type" and isinstance(v, str):
            out[k] = render(v, payload) if k not in ("customer_id_path",) else v
    if a["type"] == "add_customer_tag":
        from .logic import get_path

        out["customer_id"] = get_path(payload, a["customer_id_path"])
    return out


def execute_action(db: Session, wf: Workflow, run: WorkflowRun, a: dict) -> dict:
    payload = run.payload
    t = a["type"]
    if t == "send_email":
        from ..messaging.service import queue_message

        to = render(a["to"], payload).strip()
        if "@" not in to:
            raise ActionError(f"Recipient {to!r} is not an email address")
        m = queue_message(db, wf.store_id, channel="email", to=to, subject=render(a["subject"], payload)[:300],
                          body=render(a["body"], payload), related={"workflow_id": str(wf.id), "run_id": str(run.id)})
        db.flush()
        return {"message_id": str(m.id), "to": to}
    if t == "notify":
        from ..platform.api import notify

        notify(db, wf.store_id, "automation", render(a["title"], payload)[:200], body=render(a.get("body", ""),
               payload)[:2000], link=f"/admin/automations/runs/{run.id}", permission=a.get("permission") or None,
               severity=a.get("severity", "info"))
        return {"notified": True}
    if t == "add_customer_tag":
        from ..customers.models import Customer
        from .logic import get_path

        cid = get_path(payload, a["customer_id_path"])
        try:
            c = db.get(Customer, uuid.UUID(str(cid)), with_for_update=True)
        except ValueError as exc:
            raise ActionError(f"Payload path {a['customer_id_path']} has no customer id") from exc
        if c is None or c.store_id != wf.store_id:
            raise ActionError("Customer not found")
        c.tags = sorted({*(c.tags or []), a["tag"]})
        return {"customer_id": str(c.id), "tags": c.tags}
    if t == "call_webhook":
        from ..webhooks.models import WebhookEndpoint
        from ..webhooks.service import enqueue

        ep = db.get(WebhookEndpoint, uuid.UUID(a["endpoint_id"]))
        if ep is None or not ep.enabled:
            raise ActionError("Webhook endpoint is missing or disabled")
        did = enqueue(db, ep, run.event_type, {"workflow": wf.name, "event": payload}, source="automation")
        return {"delivery_id": str(did), "queued": True}
    if t == "ai_draft":
        from ..ai.service import queue_task

        task = queue_task(db, wf.store_id, uuid.UUID(a["agent_id"]), render(a["prompt"], payload),
                          list(wf.run_as_permissions or []), f"automation:{wf.name}")
        db.flush()
        return {"ai_task_id": str(task.id), "note": "Draft only; any proposed change still needs approval"}
    raise ActionError(f"Unknown action {t}")


def _advance(db: Session, wf: Workflow, run: WorkflowRun) -> None:
    steps = list(run.steps or [])
    for i, a in enumerate(wf.actions):
        if i < len(steps) and steps[i].get("status") == "succeeded":
            continue
        try:
            with db.begin_nested():
                res = execute_action(db, wf, run, a)
            step = {"index": i, "type": a["type"], "status": "succeeded", "result": res, "at": utcnow().isoformat()}
        except Exception as exc:  # noqa: BLE001 - recorded on the run; retried by the scheduler
            step = {"index": i, "type": a["type"], "status": "failed", "error": str(exc)[:500],
                    "at": utcnow().isoformat()}
        if i < len(steps):
            steps[i] = step
        else:
            steps.append(step)
        run.steps = json.loads(_canon(steps))
        if step["status"] == "failed":
            run.error = f"Step {i + 1} ({a['type']}): {step['error']}"
            if run.attempts >= MAX_ATTEMPTS:
                run.status, run.next_retry_at = "dead", None
            else:
                run.status = "failed"
                run.next_retry_at = utcnow() + dt.timedelta(seconds=60 * 4 ** (run.attempts - 1))
            run.finished_at = utcnow()
            return
    run.status, run.error, run.next_retry_at, run.finished_at = "succeeded", None, None, utcnow()


def run_for_event(db: Session, wf: Workflow, event_type: str, payload: dict, event_id: uuid.UUID | None) -> str:
    """Idempotent: the (workflow_id, event_id) unique key means a redelivered event never runs actions twice."""
    rid = db.execute(insert(WorkflowRun).values(
        id=uuid.uuid4(), store_id=wf.store_id, workflow_id=wf.id, workflow_version=wf.version, event_id=event_id,
        event_type=event_type, payload=json.loads(_canon(payload)), status="running", steps=[], attempts=1,
        created_at=utcnow()).on_conflict_do_nothing(index_elements=["workflow_id", "event_id"])
        .returning(WorkflowRun.id)).scalar()
    if rid is None:
        return "duplicate"
    run = db.get(WorkflowRun, rid)
    hour_ago = utcnow() - dt.timedelta(hours=1)
    recent = db.execute(select(func.count()).where(WorkflowRun.workflow_id == wf.id,
                                                   WorkflowRun.created_at >= hour_ago,
                                                   WorkflowRun.status.not_in(["skipped", "test"]))).scalar()
    wf.last_run_at = utcnow()
    if recent > wf.max_runs_per_hour:
        run.status, run.error, run.finished_at = "throttled", "Hourly run limit reached", utcnow()
        return run.status
    run.conditions_met = evaluate(wf.conditions or None, run.payload)
    if not run.conditions_met:
        run.status, run.finished_at = "skipped", utcnow()
        return run.status
    _advance(db, wf, run)
    return run.status


def _matches(trigger: str, event_type: str) -> bool:
    return trigger == event_type or (trigger.endswith(".*") and event_type.startswith(trigger[:-1]))


@on("*", name="automation.dispatch")
def dispatch(db: Session, ev) -> dict:
    if ev.store_id is None or ev.type.startswith("automation."):
        return {"runs": 0}
    wfs = db.execute(select(Workflow).where(Workflow.store_id == ev.store_id, Workflow.enabled)).scalars().all()
    out: dict[str, str] = {}
    for wf in wfs:
        if _matches(wf.trigger_event, ev.type):
            out[str(wf.id)] = run_for_event(db, wf, ev.type, ev.payload or {}, ev.id)
    return {"runs": out}


def retry_run(db: Session, run: WorkflowRun, manual: bool = False) -> WorkflowRun:
    wf = db.get(Workflow, run.workflow_id)
    if run.status not in ("failed", "dead"):
        raise AppError("not_retryable", f"Run is {run.status}")
    if manual and run.status == "dead":
        run.attempts = MAX_ATTEMPTS - 1
    run.attempts += 1
    run.status = "running"
    _advance(db, wf, run)
    return run


@periodic("automation.retry", every_seconds=30)
def retry_due(db: Session) -> dict:
    rows = db.execute(select(WorkflowRun).where(WorkflowRun.status == "failed", WorkflowRun.next_retry_at <= utcnow())
                      .limit(20).with_for_update(skip_locked=True)).scalars().all()
    for r in rows:
        retry_run(db, r)
    return {"retried": len(rows)}
