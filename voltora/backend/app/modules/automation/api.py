from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, require
from ...core.errors import NotFound
from ...core.services import EVENT_TYPES, audit
from ...db import get_db, utcnow
from .logic import COMPARE, LOGIC, evaluate
from .models import Workflow, WorkflowRun
from .service import ACTION_TYPES, preview_action, retry_run, validate_definition

router = APIRouter(prefix="/api/automation", tags=["automation"])
MODULE = {"title": "Automations", "description": "Event-triggered workflows with conditions, actions, retries and "
                                                "run history"}
MANAGE = require("automation.manage")


def wf_out(w: Workflow, stats: dict | None = None) -> dict:
    return {"id": str(w.id), "name": w.name, "description": w.description, "trigger_event": w.trigger_event,
            "conditions": w.conditions or None, "actions": w.actions, "enabled": w.enabled, "version": w.version,
            "max_runs_per_hour": w.max_runs_per_hour,
            "last_run_at": w.last_run_at.isoformat() if w.last_run_at else None,
            "updated_at": w.updated_at.isoformat() if w.updated_at else None, "stats": stats or {}}


def run_out(r: WorkflowRun, full: bool = False) -> dict:
    out = {"id": str(r.id), "workflow_id": str(r.workflow_id), "workflow_version": r.workflow_version,
           "event_id": str(r.event_id) if r.event_id else None, "event_type": r.event_type, "status": r.status,
           "conditions_met": r.conditions_met, "attempts": r.attempts, "error": r.error,
           "created_at": r.created_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
           "next_retry_at": r.next_retry_at.isoformat() if r.next_retry_at else None,
           "steps": [{k: v for k, v in s.items() if k != "result"} for s in r.steps or []]}
    if full:
        out |= {"payload": r.payload, "steps": r.steps}
    return out


@router.get("/catalog")
def catalog(p: Principal = Depends(MANAGE)):
    return {"triggers": [{"type": k, "description": v} for k, v in sorted(EVENT_TYPES.items())
                         if not k.startswith("automation.")],
            "actions": [{"type": k, **v} for k, v in ACTION_TYPES.items()],
            "operators": sorted(COMPARE | LOGIC)}


class WorkflowIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field("", max_length=2000)
    trigger_event: str = Field(max_length=80)
    conditions: dict | None = None
    actions: list[dict] = Field(max_length=20)
    enabled: bool = False
    max_runs_per_hour: int = Field(500, ge=1, le=100000)


class WorkflowPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=160)
    description: str | None = Field(None, max_length=2000)
    trigger_event: str | None = Field(None, max_length=80)
    conditions: dict | None = None
    actions: list[dict] | None = Field(None, max_length=20)
    enabled: bool | None = None
    max_runs_per_hour: int | None = Field(None, ge=1, le=100000)


def _get(db: Session, p: Principal, wid: str) -> Workflow:
    w = db.get(Workflow, parse_uuid(wid, "Workflow"))
    if w is None or w.store_id != p.store.id:
        raise NotFound("Workflow")
    return w


@router.get("/workflows")
def list_workflows(db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    rows = db.execute(select(Workflow).where(Workflow.store_id == p.store.id).order_by(Workflow.name)).scalars().all()
    stats: dict = {}
    for wid, status, n in db.execute(select(WorkflowRun.workflow_id, WorkflowRun.status, func.count())
                                     .where(WorkflowRun.store_id == p.store.id).group_by(WorkflowRun.workflow_id,
                                                                                         WorkflowRun.status)).all():
        stats.setdefault(wid, {})[status] = n
    return {"items": [wf_out(w, stats.get(w.id)) for w in rows]}


@router.post("/workflows")
def create_workflow(body: WorkflowIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    validate_definition(db, p.store.id, body.trigger_event, body.conditions, body.actions, p.permissions)
    w = Workflow(id=uuid.uuid4(), store_id=p.store.id, **body.model_dump(), run_as_permissions=sorted(p.permissions),
                 created_by=p.actor.id, updated_by=p.actor.id)
    w.conditions = body.conditions or {}
    db.add(w)
    audit(db, p.store.id, p.actor, "automation.workflow_created", "workflow", w.id,
          {"trigger": w.trigger_event, "actions": [a["type"] for a in w.actions], "enabled": w.enabled})
    db.commit()
    return wf_out(w)


@router.get("/workflows/{wid}")
def get_workflow(wid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    return wf_out(_get(db, p, wid))


@router.patch("/workflows/{wid}")
def update_workflow(wid: str, body: WorkflowPatch, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    w = _get(db, p, wid)
    fields = body.model_fields_set
    trigger = body.trigger_event or w.trigger_event
    conditions = body.conditions if "conditions" in fields else (w.conditions or None)
    actions = body.actions if body.actions is not None else w.actions
    definition_changed = bool(fields & {"trigger_event", "conditions", "actions"})
    if definition_changed:
        validate_definition(db, p.store.id, trigger, conditions, actions, p.permissions)
    for k in ("name", "description", "enabled", "max_runs_per_hour"):
        if getattr(body, k) is not None:
            setattr(w, k, getattr(body, k))
    if definition_changed:
        w.trigger_event, w.conditions, w.actions = trigger, conditions or {}, actions
        w.version += 1
        w.run_as_permissions = sorted(p.permissions)
    w.updated_by = p.actor.id
    audit(db, p.store.id, p.actor, "automation.workflow_updated", "workflow", w.id,
          {"fields": sorted(fields), "version": w.version, "enabled": w.enabled})
    db.commit()
    return wf_out(w)


@router.delete("/workflows/{wid}")
def delete_workflow(wid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    w = _get(db, p, wid)
    db.delete(w)
    audit(db, p.store.id, p.actor, "automation.workflow_deleted", "workflow", w.id, {"name": w.name})
    db.commit()
    return {"ok": True}


class TestIn(BaseModel):
    payload: dict = Field(default_factory=dict)


@router.post("/workflows/{wid}/test")
def test_workflow(wid: str, body: TestIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    """Dry run: evaluates conditions against the sample payload and renders each action. Nothing is sent."""
    w = _get(db, p, wid)
    met = evaluate(w.conditions or None, body.payload)
    steps = [{"index": i, "type": a["type"], "status": "would_run" if met else "not_reached",
              "preview": preview_action(a, body.payload)} for i, a in enumerate(w.actions)]
    run = WorkflowRun(store_id=p.store.id, workflow_id=w.id, workflow_version=w.version, event_id=None,
                      event_type=w.trigger_event, payload=body.payload, status="test", conditions_met=met,
                      steps=steps, finished_at=utcnow())
    db.add(run)
    db.commit()
    return {"conditions_met": met, "steps": steps, "run_id": str(run.id),
            "note": "Dry run: no email, notification, tag, webhook or AI task was created."}


@router.get("/runs")
def runs(workflow_id: str | None = None, status: str | None = None, page: int = Query(1, ge=1),
         db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    q = select(WorkflowRun).where(WorkflowRun.store_id == p.store.id)
    if workflow_id:
        q = q.where(WorkflowRun.workflow_id == parse_uuid(workflow_id, "Workflow"))
    if status:
        q = q.where(WorkflowRun.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(WorkflowRun.created_at.desc()).offset((page - 1) * 50).limit(50)).scalars()
    return {"items": [run_out(r) for r in rows], "total": total, "page": page, "page_size": 50}


def _run(db: Session, p: Principal, rid: str) -> WorkflowRun:
    r = db.get(WorkflowRun, parse_uuid(rid, "Run"))
    if r is None or r.store_id != p.store.id:
        raise NotFound("Run")
    return r


@router.get("/runs/{rid}")
def run_detail(rid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    return run_out(_run(db, p, rid), full=True)


@router.post("/runs/{rid}/retry")
def retry(rid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    r = db.execute(select(WorkflowRun).where(WorkflowRun.id == parse_uuid(rid, "Run"),
                                             WorkflowRun.store_id == p.store.id).with_for_update()).scalar_one_or_none()
    if r is None:
        raise NotFound("Run")
    retry_run(db, r, manual=True)
    audit(db, p.store.id, p.actor, "automation.run_retried", "workflow_run", r.id, {"status": r.status})
    db.commit()
    return run_out(r, full=True)
