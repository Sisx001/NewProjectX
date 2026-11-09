"""`alembic upgrade head` on an empty database must produce exactly the schema the models declare."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.config import get_settings
from app.db import Base
from app.main import discover_modules
from app.modules.ops.dbtools import scratch_database

BACKEND = Path(__file__).resolve().parents[1]


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "VOLTORA_DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, capture_output=True,
                          text=True, timeout=300)


def test_upgrade_head_matches_models_and_is_reversible():
    discover_modules()
    with scratch_database(get_settings().database_url, "voltora_mig") as url:
        up = _alembic(url, "upgrade", "head")
        assert up.returncode == 0, up.stderr[-2000:]
        eng = create_engine(url)
        try:
            with eng.connect() as conn:
                diff = compare_metadata(MigrationContext.configure(
                    conn, opts={"compare_type": True, "compare_server_default": True}), Base.metadata)
                assert diff == [], f"models differ from migrations: {diff[:5]}"
                tables = set(inspect(conn).get_table_names())
            assert {"stores", "audit_events", "backups", "users"} <= tables
        finally:
            eng.dispose()
        again = _alembic(url, "upgrade", "head")  # idempotent re-run
        assert again.returncode == 0, again.stderr[-1000:]
        heads = _alembic(url, "heads")
        assert heads.returncode == 0 and len(heads.stdout.strip().splitlines()) == 1, "single linear head expected"
