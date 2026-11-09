#!/bin/sh
# Applies database migrations before the API starts (single-instance deployment). Set VOLTORA_AUTO_MIGRATE=0 to disable.
set -eu
cd /app/backend
if [ "${VOLTORA_AUTO_MIGRATE:-1}" = "1" ]; then
  echo "voltora: applying database migrations"
  alembic upgrade head
fi
exec "$@"
