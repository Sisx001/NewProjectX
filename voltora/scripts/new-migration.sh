#!/usr/bin/env bash
# Autogenerate a new revision: a scratch DB is upgraded to the current head, then diffed against the models.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: scripts/new-migration.sh \"message\"" >&2; exit 2; }
cd "$(dirname "$0")/../backend"
SCRATCH="$(uv run python -m app.modules.ops.dbtools create-scratch)"
trap 'uv run python -m app.modules.ops.dbtools drop "$SCRATCH" >/dev/null 2>&1 || true' EXIT
export VOLTORA_DATABASE_URL="$SCRATCH"
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "$1"
echo "Review the new file in backend/alembic/versions/ (renames, data moves and default changes need manual edits)."
