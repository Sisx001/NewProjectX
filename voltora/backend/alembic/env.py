"""Alembic environment: same module discovery as the app, URL from app settings (VOLTORA_DATABASE_URL)."""
from __future__ import annotations

import logging

from alembic.operations import ops
from sqlalchemy import Sequence, create_engine, pool, text

from alembic import context
from app.config import get_settings
from app.db import Base
from app.main import discover_modules

discover_modules()  # imports every app.modules.<m>.models so Base.metadata is complete
# Importing app.main may attach the DB log shipper; migrations must not write log rows.
for _h in list(logging.getLogger().handlers):
    if type(_h).__name__ == "DbLogHandler":
        logging.getLogger().removeHandler(_h)

config = context.config
target_metadata = Base.metadata
URL = get_settings().database_url


def _declared_sequences():
    seqs = {s.name: s for s in getattr(Base.metadata, "_sequences", {}).values()}
    for t in Base.metadata.tables.values():
        for c in t.columns:
            if isinstance(c.default, Sequence):
                seqs[c.default.name] = c.default
    return list(seqs.values())


def add_missing_sequences(ctx, revision, directives):
    """Autogenerate never emits CREATE SEQUENCE; add it (before the tables that use it) when the DB lacks the sequence."""
    script = directives[0]
    existing = {r[0] for r in ctx.connection.execute(text("SELECT sequencename FROM pg_sequences WHERE schemaname = current_schema()"))}
    for s in reversed([s for s in _declared_sequences() if s.name not in existing]):
        start, inc = s.start or 1, s.increment or 1
        script.upgrade_ops.ops.insert(0, ops.ExecuteSQLOp(f"CREATE SEQUENCE {s.name} START WITH {start} INCREMENT BY {inc}"))
        script.downgrade_ops.ops.append(ops.ExecuteSQLOp(f"DROP SEQUENCE {s.name}"))


def run_migrations_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(URL, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                          process_revision_directives=add_missing_sequences,
                          compare_server_default=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
