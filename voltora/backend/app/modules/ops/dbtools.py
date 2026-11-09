"""PostgreSQL helpers shared by backups, migration tests and scripts: URL parsing, scratch databases, pg_* binaries."""
from __future__ import annotations

import os
import shutil
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

PG_DIRS = ("/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/17/bin", "/usr/lib/postgresql/15/bin",
           "/usr/pgsql-16/bin", "/opt/homebrew/opt/postgresql@16/bin")


def pg_binary(name: str) -> str | None:
    """Locate pg_dump/pg_restore/psql: PATH first, then the usual install directories."""
    found = shutil.which(name)
    if found:
        return found
    for d in PG_DIRS:
        p = Path(d) / name
        if p.exists():
            return str(p)
    return None


def prerequisites() -> dict:
    return {n: pg_binary(n) for n in ("pg_dump", "pg_restore", "psql")}


def parse_url(database_url: str) -> URL:
    return make_url(database_url)


def with_database(database_url: str, dbname: str) -> str:
    return parse_url(database_url).set(database=dbname).render_as_string(hide_password=False)


def pg_env(database_url: str) -> dict[str, str]:
    """libpq environment (keeps the password out of argv)."""
    u = parse_url(database_url)
    env = dict(os.environ)
    if u.host:
        env["PGHOST"] = u.host
    if u.port:
        env["PGPORT"] = str(u.port)
    if u.username:
        env["PGUSER"] = u.username
    if u.password:
        env["PGPASSWORD"] = u.password
    return env


def _admin_engine(database_url: str):
    return create_engine(with_database(database_url, "postgres"), isolation_level="AUTOCOMMIT")


def create_scratch_database(database_url: str, prefix: str = "voltora_scratch") -> str:
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    eng = _admin_engine(database_url)
    try:
        with eng.connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        eng.dispose()
    return with_database(database_url, name)


def drop_database(database_url: str) -> None:
    name = parse_url(database_url).database
    if not name or name in ("postgres", "template0", "template1"):
        raise ValueError("refusing to drop a system database")
    eng = _admin_engine(database_url)
    try:
        with eng.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        eng.dispose()


@contextmanager
def scratch_database(database_url: str, prefix: str = "voltora_scratch"):
    url = create_scratch_database(database_url, prefix)
    try:
        yield url
    finally:
        drop_database(url)


if __name__ == "__main__":  # used by scripts/*.sh
    from app.config import get_settings

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "create-scratch":
        print(create_scratch_database(get_settings().database_url, "voltora_migrate"))
    elif cmd == "drop":
        drop_database(sys.argv[2])
    else:
        print("usage: dbtools create-scratch | drop <url>", file=sys.stderr)
        sys.exit(2)
