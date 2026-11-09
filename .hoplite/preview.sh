#!/bin/sh
# Managed preview: local PostgreSQL + one uvicorn process serving the API and the built web app.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)/voltora"
PGBIN=/usr/lib/postgresql/16/bin
PGDATA="$ROOT/.data/pg"
PORT="${PORT:-3000}"
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  mkdir -p "$PGDATA"; chown -R hoplite "$ROOT/.data" 2>/dev/null || true
  su hoplite -s /bin/sh -c "$PGBIN/initdb -D $PGDATA -U voltora --auth=trust -E UTF8 >/dev/null"
  printf "port=5433\nlisten_addresses='127.0.0.1'\nunix_socket_directories='/tmp'\n" >> "$PGDATA/postgresql.conf"
fi
if ! $PGBIN/pg_isready -h 127.0.0.1 -p 5433 >/dev/null 2>&1; then
  su hoplite -s /bin/sh -c "$PGBIN/pg_ctl -D $PGDATA -l $ROOT/.data/pg.log start" >/dev/null
  sleep 2
fi
psql -h 127.0.0.1 -p 5433 -U voltora -d postgres -tc "select 1 from pg_database where datname='voltora_preview'" | grep -q 1 \
  || psql -h 127.0.0.1 -p 5433 -U voltora -d postgres -qc "create database voltora_preview"
export VOLTORA_DATABASE_URL=postgresql+psycopg://voltora@127.0.0.1:5433/voltora_preview
export VOLTORA_DATA_DIR="$ROOT/.data/preview"
cd "$ROOT/web"
[ -d node_modules ] || npm ci --no-audit --no-fund
npx vite build >/dev/null
cd "$ROOT/backend"
uv sync -q
if ls alembic/versions/*.py >/dev/null 2>&1; then
  uv run alembic upgrade head
else
  uv run python -c "from app.main import create_app; from app.db import Base,get_engine; create_app(start_worker=False); Base.metadata.create_all(get_engine())"
fi
exec uv run uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips 127.0.0.1
