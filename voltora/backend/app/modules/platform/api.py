from __future__ import annotations

import platform as pyplatform
import time

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ...config import CORE_VERSION, get_settings
from ...core.deps import Principal, current_store, parse_uuid, principal, require, staff
from ...core.errors import NotFound
from ...core.services import (
    EVENT_TYPES,
    PERIODIC_JOBS,
    SETTINGS,
    audit,
    get_setting,
    limiter,
    set_settings,
    setting,
    verify_audit_chain,
)
from ...db import get_db, utcnow
from .models import AuditEvent, ConfigRevision, LogRecord, Notification, OutboxEvent, SettingValue

router = APIRouter(prefix="/api", tags=["platform"])
STARTED = time.time()

# Canonical store identity settings (one definition each; every screen reads these).
setting(key="store.support_email", type="email", default="", label="Support email", group="Store", public=True)
setting(key="store.support_phone", type="string", default="", label="Support phone", group="Store", public=True,
        max=32)
setting(key="store.tagline", type="string", default="", label="Tagline", group="Store", public=True, max=160)
setting(key="store.address", type="text", default="", label="Business address", group="Store", public=True, max=500)
setting(key="store.maintenance_mode", type="bool", default=False, label="Maintenance mode", group="Store",
        description="Storefront shows a maintenance page; staff can still sign in.", write_permission="system.manage",
        public=True)
setting(key="store.announcement", type="string", default="", label="Announcement bar text", group="Store",
        public=True, max=200)


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    return {"status": "ok", "version": CORE_VERSION}


@router.get("/storefront/config")
def storefront_config(db: Session = Depends(get_db)):
    store = current_store(db)
    public = {k: get_setting(db, store.id, k) for k, d in SETTINGS.items() if d.public}
    return {"store": {"name": store.name, "currency": store.currency, "locale": store.locale,
                      "timezone": store.timezone, "environment": store.environment}, "settings": public,
            "payments_mode": get_settings().payments_mode}


# ---------------------------------------------------------------- settings
@router.get("/settings")
def list_settings(db: Session = Depends(get_db), p: Principal = Depends(require("settings.read"))):
    values = {r.key: r for r in db.execute(select(SettingValue).where(SettingValue.store_id == p.store.id)).scalars()}
    groups: dict[str, list] = {}
    for d in SETTINGS.values():
        if not p.can(d.read_permission):
            continue
        row = values.get(d.key)
        groups.setdefault(d.group, []).append({
            "key": d.key, "type": d.type, "label": d.label, "description": d.description, "choices": d.choices,
            "min": d.min, "max": d.max, "default": d.default, "value": row.value if row else d.default,
            "customized": row is not None, "writable": p.can(d.write_permission)})
    return {"groups": [{"name": k, "settings": v} for k, v in sorted(groups.items())]}


class SettingsPatch(BaseModel):
    changes: dict
    note: str | None = None


@router.patch("/settings")
def patch_settings(body: SettingsPatch, db: Session = Depends(get_db), p: Principal = Depends(staff)):
    diff = set_settings(db, p.store.id, body.changes, p.actor, body.note, can=p.can)
    db.commit()
    return {"changed": diff}


@router.get("/settings/revisions")
def revisions(db: Session = Depends(get_db), p: Principal = Depends(require("settings.read"))):
    rows = db.execute(select(ConfigRevision).where(ConfigRevision.store_id == p.store.id)
                      .order_by(ConfigRevision.created_at.desc()).limit(100)).scalars()
    return {"items": [{"id": str(r.id), "changes": r.changes, "actor_id": str(r.actor_id) if r.actor_id else None,
                       "note": r.note, "created_at": r.created_at.isoformat()} for r in rows]}


@router.post("/settings/revisions/{revision_id}/revert")
def revert_revision(revision_id: str, db: Session = Depends(get_db), p: Principal = Depends(staff)):
    r = db.get(ConfigRevision, parse_uuid(revision_id, "Revision"))
    if r is None or r.store_id != p.store.id:
        raise NotFound("Revision")
    changes = {k: v["from"] for k, v in r.changes.items() if k in SETTINGS}
    diff = set_settings(db, p.store.id, changes, p.actor, f"Revert of revision {r.id}", can=p.can)
    db.commit()
    return {"changed": diff}


# ---------------------------------------------------------------- audit
@router.get("/audit")
def audit_list(action: str | None = None, target_type: str | None = None, target_id: str | None = None,
               page: int = Query(1, ge=1), db: Session = Depends(get_db),
               p: Principal = Depends(require("audit.read"))):
    q = select(AuditEvent).where(AuditEvent.store_id == p.store.id)
    if action:
        q = q.where(AuditEvent.action.ilike(f"{action}%"))
    if target_type:
        q = q.where(AuditEvent.target_type == target_type)
    if target_id:
        q = q.where(AuditEvent.target_id == target_id)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(AuditEvent.seq.desc()).offset((page - 1) * 50).limit(50)).scalars()
    return {"items": [{"seq": e.seq, "action": e.action, "actor_kind": e.actor_kind, "actor_label": e.actor_label,
                       "target_type": e.target_type, "target_id": e.target_id, "data": e.data, "ip": e.ip,
                       "created_at": e.created_at.isoformat()} for e in rows], "total": total, "page": page,
            "page_size": 50}


@router.get("/audit/verify")
def audit_verify(db: Session = Depends(get_db), p: Principal = Depends(require("audit.read"))):
    return verify_audit_chain(db, p.store.id)


# ---------------------------------------------------------------- notifications
@router.get("/notifications")
def notifications(db: Session = Depends(get_db), p: Principal = Depends(staff)):
    uid = str(p.user.id) if p.user else None
    rows = db.execute(select(Notification).where(Notification.store_id == p.store.id)
                      .order_by(Notification.created_at.desc()).limit(60)).scalars()
    items = []
    for n in rows:
        if n.user_id and (p.user is None or n.user_id != p.user.id):
            continue
        if n.permission and not p.can(n.permission):
            continue
        items.append({"id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body, "link": n.link,
                      "severity": n.severity, "read": uid in (n.read_by or []), "created_at": n.created_at.isoformat()})
    return {"items": items, "unread": sum(1 for i in items if not i["read"])}


@router.post("/notifications/read")
def mark_read(db: Session = Depends(get_db), p: Principal = Depends(staff)):
    uid = str(p.user.id)
    for n in db.execute(select(Notification).where(Notification.store_id == p.store.id)).scalars():
        if uid not in (n.read_by or []):
            n.read_by = [*(n.read_by or []), uid]
    db.commit()
    return {"ok": True}


def notify(db: Session, store_id, kind: str, title: str, body: str | None = None, link: str | None = None,
           permission: str | None = None, severity: str = "info", user_id=None) -> None:
    db.add(Notification(store_id=store_id, kind=kind, title=title, body=body, link=link, permission=permission,
                        severity=severity, user_id=user_id))


# ---------------------------------------------------------------- system console
@router.get("/system/health")
def system_health(request: Request, db: Session = Depends(get_db), p: Principal = Depends(require("system.read"))):
    s = get_settings()
    pg = db.execute(text("select version()")).scalar()
    size = db.execute(text("select pg_database_size(current_database())")).scalar()
    conns = db.execute(text("select count(*) from pg_stat_activity where datname = current_database()")).scalar()
    counts = dict(db.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)).all())
    worker = getattr(request.app.state, "worker", None)
    from ..messaging.models import OutboundMessage

    msg_counts = dict(db.execute(select(OutboundMessage.status, func.count()).where(
        OutboundMessage.store_id == p.store.id).group_by(OutboundMessage.status)).all())
    return {
        "version": CORE_VERSION, "env": s.env, "payments_mode": s.payments_mode,
        "external_network": s.external_network, "uptime_seconds": int(time.time() - STARTED),
        "python": pyplatform.python_version(),
        "database": {"server": pg.split(",")[0], "size_bytes": size, "connections": conns},
        "worker": {"running": bool(worker and worker.is_alive()),
                   "last_tick": worker.last_tick.isoformat() if worker and worker.last_tick else None},
        "outbox": counts,
        "messages": msg_counts,
        "jobs": [{"name": j.name, "every_seconds": j.every_seconds, "last_error": j.last_error,
                  "last_result": j.last_result} for j in PERIODIC_JOBS],
        "rate_limit_blocks": dict(limiter.blocked),
    }


@router.get("/system/events")
def outbox_list(status: str | None = None, page: int = Query(1, ge=1), db: Session = Depends(get_db),
                p: Principal = Depends(require("system.read"))):
    q = select(OutboxEvent).where((OutboxEvent.store_id == p.store.id) | OutboxEvent.store_id.is_(None))
    if status:
        q = q.where(OutboxEvent.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(OutboxEvent.created_at.desc()).offset((page - 1) * 50).limit(50)).scalars()
    return {"items": [{"id": str(e.id), "type": e.type, "status": e.status, "attempts": e.attempts,
                       "last_error": e.last_error, "results": e.results, "created_at": e.created_at.isoformat(),
                       "processed_at": e.processed_at.isoformat() if e.processed_at else None} for e in rows],
            "total": total, "page": page, "page_size": 50, "event_types": EVENT_TYPES}


@router.post("/system/events/{event_id}/retry")
def outbox_retry(event_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("system.manage"))):
    e = db.get(OutboxEvent, parse_uuid(event_id, "Event"))
    if e is None:
        raise NotFound("Event")
    e.status, e.next_attempt_at = "pending", utcnow()
    if e.attempts >= 6:
        e.attempts = 5  # one more attempt before dead-lettering again
    audit(db, p.store.id, p.actor, "system.event_retry", "event", e.id)
    db.commit()
    return {"ok": True}


@router.get("/system/logs")
def logs(level: str | None = None, q: str | None = None, request_id: str | None = None,
         page: int = Query(1, ge=1), db: Session = Depends(get_db), p: Principal = Depends(require("system.read"))):
    stmt = select(LogRecord)
    if level:
        stmt = stmt.where(LogRecord.level == level.upper())
    if q:
        stmt = stmt.where(LogRecord.message.ilike(f"%{q}%"))
    if request_id:
        stmt = stmt.where(LogRecord.request_id == request_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.execute(stmt.order_by(LogRecord.id.desc()).offset((page - 1) * 100).limit(100)).scalars()
    return {"items": [{"id": r.id, "level": r.level, "logger": r.logger, "message": r.message,
                       "request_id": r.request_id, "data": r.data, "created_at": r.created_at.isoformat()}
                      for r in rows], "total": total, "page": page, "page_size": 100}


@router.get("/system/capabilities")
def capabilities(request: Request, p: Principal = Depends(principal)):
    """Machine-readable map of what this installation can actually do (feeds admin nav and the AI capability graph)."""
    from ...capabilities import build_capability_graph

    return build_capability_graph(request.app, p)


from .install import router as install_router  # noqa: E402

extra_routers = [install_router]
MODULE = {"title": "Platform", "description": "Store settings, audit trail, notifications, health, jobs, logs, installer"}
