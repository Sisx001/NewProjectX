#!/usr/bin/env bash
# Exit 0 when the API answers /api/health with 200 within the retry window. Usage: healthcheck.sh [url] [attempts]
set -uo pipefail
URL="${1:-http://127.0.0.1:8000/api/health}"
ATTEMPTS="${2:-30}"
for i in $(seq 1 "$ATTEMPTS"); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "$URL" || true)"
  if [ "$code" = "200" ]; then echo "healthy ($URL)"; exit 0; fi
  sleep 2
done
echo "UNHEALTHY: $URL did not return 200 after $ATTEMPTS attempts (last: ${code:-none})" >&2
exit 1
