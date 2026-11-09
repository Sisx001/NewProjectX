from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, require
from ...core.errors import AppError, Conflict, NotFound
from ...core.services import audit, get_setting
from ...db import get_db
from ..platform.models import BackupRecord
from . import backup
from .dbtools import prerequisites
from .service import BACKUP_KEYS

router = APIRouter(prefix="/api/system/backups", tags=["ops"])
MODULE = {"title": "Operations", "description": "Backups, restore drills and platform maintenance"}


class CreateBody(BaseModel):
    encrypt: bool | None = None


def _get(db: Session, backup_id: str) -> BackupRecord:
    rec = db.get(BackupRecord, parse_uuid(backup_id, "Backup"))
    if rec is None:
        raise NotFound("Backup")
    return rec


@router.get("")
def list_backups(db: Session = Depends(get_db), p: Principal = Depends(require("system.read"))):
    rows = db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(200)).scalars().all()
    ok = [r for r in rows if r.status == "succeeded"]
    pre = prerequisites()
    return {
        "items": [backup.serialize(r) for r in rows],
        "prerequisites": {"pg_dump": bool(pre["pg_dump"]), "pg_restore": bool(pre["pg_restore"]),
                          "restore_drill_ready": bool(pre["pg_restore"])},
        "last_success_at": ok[0].finished_at.isoformat() if ok and ok[0].finished_at else None,
        "last_verified_at": max((r.restore_verified_at for r in ok if r.restore_verified_at), default=None),
        "schedule": {k.removeprefix("backup."): get_setting(db, p.store.id, k) for k in BACKUP_KEYS},
        "can_manage": p.can("system.manage"),
    }


@router.post("", status_code=202)
def create(body: CreateBody, tasks: BackgroundTasks, db: Session = Depends(get_db),
           p: Principal = Depends(require("system.manage"))):
    if not prerequisites()["pg_dump"]:
        raise AppError("prerequisite_missing", "pg_dump is not installed on this server (install postgresql-client-16).",
                       status=409)
    if db.execute(select(BackupRecord.id).where(BackupRecord.status == "running", BackupRecord.kind == "full")
                  .limit(1)).scalar():
        raise Conflict("backup_running", "A backup is already running")
    encrypt = body.encrypt if body.encrypt is not None else bool(get_setting(db, p.store.id, "backup.encrypt"))
    rec = backup.start_record(db, p.user.id if p.user else None, encrypt)
    tasks.add_task(backup.run_backup, rec.id, p.store.id, p.actor)
    return {"id": str(rec.id), "status": "running"}


@router.post("/{backup_id}/verify-restore", status_code=202)
def verify(backup_id: str, tasks: BackgroundTasks, db: Session = Depends(get_db),
           p: Principal = Depends(require("system.manage"))):
    rec = _get(db, backup_id)
    if rec.status != "succeeded":
        raise Conflict("not_ready", "Only succeeded backups can be verified")
    if not prerequisites()["pg_restore"]:
        raise AppError("prerequisite_missing", "pg_restore is not installed on this server.", status=409)
    if ((rec.restore_report or {}).get("drill") or {}).get("status") == "running":
        raise Conflict("drill_running", "A restore drill is already running for this backup")

    def job():
        from ...db import session_factory

        with session_factory()() as s:
            try:
                backup.verify_restore(s, rec.id, p.store.id, p.actor)
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                r = s.get(BackupRecord, rec.id)
                if r is not None:
                    r.restore_report = {**(r.restore_report or {}), "drill": {"ok": False, "steps": [
                        {"name": "drill", "ok": False, "detail": str(exc)[:400]}]}}
                    s.commit()

    tasks.add_task(job)
    return {"id": str(rec.id), "status": "verifying"}


@router.get("/{backup_id}/download")
def download(backup_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("system.manage"))):
    rec = _get(db, backup_id)
    path = Path(rec.path or "")
    if rec.status != "succeeded" or not path.is_file() or backup.backup_dir().resolve() not in path.resolve().parents:
        raise NotFound("Backup file")
    audit(db, p.store.id, p.actor, "backup.downloaded", "backup", rec.id, {"encrypted": rec.encrypted})
    db.commit()
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.delete("/{backup_id}")
def remove(backup_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("system.manage"))):
    rec = _get(db, backup_id)
    if rec.status == "running":
        raise Conflict("backup_running", "Wait for the running backup to finish")
    backup.delete_record(db, p.store.id, rec, p.actor)
    db.commit()
    return {"ok": True}
