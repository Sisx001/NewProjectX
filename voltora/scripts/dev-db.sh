#!/usr/bin/env bash
# Start a throw-away local PostgreSQL on 127.0.0.1:5433 (user voltora, db voltora) unless one already answers there.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
[ -x "$PGBIN/pg_ctl" ] || PGBIN="$(dirname "$(command -v pg_ctl || true)")"
PORT="${VOLTORA_DEV_PG_PORT:-5433}"
if "$PGBIN/pg_isready" -q -h 127.0.0.1 -p "$PORT"; then echo "PostgreSQL already running on :$PORT"; exit 0; fi
D="$ROOT/.data/pg"
if [ ! -d "$D" ]; then
  mkdir -p "$D"
  "$PGBIN/initdb" -D "$D" -U voltora --auth=trust -E UTF8 >/dev/null
fi
"$PGBIN/pg_ctl" -D "$D" -o "-p $PORT -k /tmp -c listen_addresses=127.0.0.1" -l "$ROOT/.data/pg.log" -w start
"$PGBIN/createdb" -h 127.0.0.1 -p "$PORT" -U voltora voltora 2>/dev/null || true
echo "PostgreSQL ready on 127.0.0.1:$PORT (trust auth, development only)"
