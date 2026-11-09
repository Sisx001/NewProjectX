"""Alembic environment: same module discovery as the app, URL from app settings (VOLTORA_DATABASE_URL)."""
from __future__ import annotations

import logging

from alembic import context
from sqlalchemy import create_engine, pool

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


def run_migrations_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(URL, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                          compare_server_default=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
