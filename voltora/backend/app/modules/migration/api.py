from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.deps import Principal, require
from ...db import get_db
from .models import LegacyImportRun

router = APIRouter(prefix="/api/system/migration", tags=["migration"])
MODULE = {"title": "Legacy migration", "description": "Import from a legacy VOLTORA (MongoDB) export; run via CLI"}


@router.get("/runs")
def runs(db: Session = Depends(get_db), p: Principal = Depends(require("system.manage"))):
    """History of applied imports (dry runs are not persisted). Imports are run with the CLI, see docs/DEPLOYMENT.md."""
    rows = db.execute(select(LegacyImportRun).where(LegacyImportRun.store_id == p.store.id)
                      .order_by(LegacyImportRun.created_at.desc()).limit(50)).scalars()
    return {"items": [{"id": str(r.id), "mode": r.mode, "source": r.source, "summary": r.summary,
                       "created_at": r.created_at.isoformat()} for r in rows]}
