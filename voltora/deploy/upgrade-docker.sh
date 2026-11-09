#!/usr/bin/env bash
# Docker Compose upgrade with backup, health check and image-tag rollback.
#   deploy/upgrade-docker.sh           # backup -> build new image -> migrate+restart -> health check (auto-revert on failure)
#   deploy/upgrade-docker.sh --rollback <previous-tag>
# Run from the repository root. UNVERIFIED in the authoring environment (no Docker daemon).
set -euo pipefail
cd "$(dirname "$0")/.."
C="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"

if [ "${1:-}" = "--rollback" ]; then
  TAG="${2:?previous image tag required}"
  VOLTORA_IMAGE_TAG="$TAG" VOLTORA_AUTO_MIGRATE=0 $C up -d --no-build api
  echo "Rolled the API back to image tag $TAG. The database schema was NOT reverted (restore the pre-upgrade backup if a migration must be undone)."
  exit 0
fi

PREV_TAG="$(grep -E '^VOLTORA_IMAGE_TAG=' deploy/.env | cut -d= -f2- || true)"; PREV_TAG="${PREV_TAG:-local}"
NEW_TAG="$(date -u +%Y%m%d%H%M%S)"
echo "1/4 pre-upgrade backup + restore drill"
$C exec -T api python -m app.modules.ops.backup create --encrypt
$C exec -T api python -m app.modules.ops.backup verify
docker tag "voltora:${PREV_TAG}" "voltora:rollback-${NEW_TAG}" 2>/dev/null || true
echo "2/4 build voltora:${NEW_TAG}"
VOLTORA_IMAGE_TAG="$NEW_TAG" $C build api
echo "3/4 start (the entrypoint runs alembic upgrade head)"
VOLTORA_IMAGE_TAG="$NEW_TAG" $C up -d api
echo "4/4 health check"
for i in $(seq 1 30); do
  if [ "$($C ps --format '{{.Health}}' api)" = "healthy" ]; then
    sed -i "s/^VOLTORA_IMAGE_TAG=.*/VOLTORA_IMAGE_TAG=${NEW_TAG}/" deploy/.env
    echo "Upgraded to ${NEW_TAG}. Rollback: deploy/upgrade-docker.sh --rollback rollback-${NEW_TAG}"
    exit 0
  fi
  sleep 3
done
echo "UNHEALTHY: reverting API to ${PREV_TAG}" >&2
VOLTORA_IMAGE_TAG="$PREV_TAG" VOLTORA_AUTO_MIGRATE=0 $C up -d --no-build api
exit 1
