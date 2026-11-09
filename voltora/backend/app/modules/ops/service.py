"""Ops settings and scheduled jobs (backups). Registered at import via module discovery."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.services import SYSTEM, get_setting, periodic, setting
from ...db import utcnow
from ..platform.models import BackupRecord, Store

setting(key="backup.enabled", type="bool", default=False, label="Scheduled backups", group="Backups",
        description="Create a backup automatically on the interval below. Requires pg_dump on the server.",
        read_permission="system.read", write_permission="system.manage")
setting(key="backup.interval_hours", type="int", default=24, min=1, max=720, label="Backup interval (hours)",
        group="Backups", read_permission="system.read", write_permission="system.manage")
setting(key="backup.encrypt", type="bool", default=True, label="Encrypt scheduled backups", group="Backups",
        description="AES-256-GCM, key derived from the vault key. Keep the vault key safe: it is needed to restore.",
        read_permission="system.read", write_permission="system.manage")
setting(key="backup.retention_count", type="int", default=14, min=1, max=1000, label="Keep at least this many backups",
        group="Backups", read_permission="system.read", write_permission="system.manage")
setting(key="backup.retention_days", type="int", default=30, min=1, max=3650, label="Keep backups for (days)",
        group="Backups", description="A backup is deleted only when it is beyond the count AND older than the days.",
        read_permission="system.read", write_permission="system.manage")
setting(key="backup.verify_after_create", type="bool", default=True, label="Run a restore drill after each scheduled backup",
        group="Backups", read_permission="system.read", write_permission="system.manage")

BACKUP_KEYS = ["backup.enabled", "backup.interval_hours", "backup.encrypt", "backup.retention_count",
               "backup.retention_days", "backup.verify_after_create"]


@periodic("ops.scheduled_backup", every_seconds=900)
def scheduled_backup(db: Session):
    from . import backup

    store = db.execute(select(Store).order_by(Store.created_at).limit(1)).scalar()
    if store is None or not get_setting(db, store.id, "backup.enabled"):
        return {"skipped": "disabled"}
    interval = get_setting(db, store.id, "backup.interval_hours")
    last = db.execute(select(BackupRecord.created_at).where(BackupRecord.status.in_(("succeeded", "running")))
                      .order_by(BackupRecord.created_at.desc()).limit(1)).scalar()
    if last is not None and (utcnow() - last).total_seconds() < interval * 3600:
        return {"skipped": "not due"}
    rec = backup.create_backup(db, store.id, SYSTEM, encrypt=get_setting(db, store.id, "backup.encrypt"))
    out = {"backup_id": str(rec.id), "status": rec.status}
    if rec.status == "succeeded" and get_setting(db, store.id, "backup.verify_after_create"):
        out["drill_ok"] = backup.verify_restore(db, rec.id, store.id, SYSTEM)["ok"]
    out["pruned"] = backup.prune(db, store.id, get_setting(db, store.id, "backup.retention_count"),
                                 get_setting(db, store.id, "backup.retention_days"))
    return out
