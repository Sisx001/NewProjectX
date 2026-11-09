#!/usr/bin/env bash
# Wrapper for export-legacy-mongo.py. Set LEGACY_PYTHON to an interpreter that has pymongo (e.g. the legacy venv).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${LEGACY_PYTHON:-python3}"
exec "$PY" "$HERE/export-legacy-mongo.py" "$@"
