#!/usr/bin/env bash
# Upgrade / rollback for the bare-metal install (see scripts/install-ubuntu.sh for the layout).
#
#   sudo scripts/upgrade-ubuntu.sh [--dry-run]     backup -> new release -> migrate -> restart -> health check (auto-revert on failure)
#   sudo scripts/upgrade-ubuntu.sh --rollback      point `current` at the previous release and restart
#
# Database rollback: migrations are forward-only in production. If a migration must be undone, restore the pre-upgrade backup
# into a NEW database and switch VOLTORA_DATABASE_URL (docs/OPERATIONS.md "Restore"). The pre-upgrade backup id is printed.
set -euo pipefail
DRY=0; ROLLBACK=0
for a in "$@"; do case "$a" in --dry-run) DRY=1 ;; --rollback) ROLLBACK=1 ;; *) echo "unknown option $a" >&2; exit 2 ;; esac; done
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/voltora; ENVF=/etc/voltora/voltora.env
run() { printf '+ %s\n' "$*"; if [ "$DRY" = 0 ]; then "$@"; fi; }
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || { echo "Run as root or use --dry-run" >&2; exit 1; }

CUR="$(readlink -f "$PREFIX/current" 2>/dev/null || true)"
[ -n "$CUR" ] || { echo "No current release under $PREFIX; run install-ubuntu.sh first." >&2; exit 1; }

previous_release() { ls -1dt "$PREFIX"/releases/*/ 2>/dev/null | sed 's:/$::' | grep -vx "$CUR" | head -1; }

if [ "$ROLLBACK" = 1 ]; then
  PREV="$(previous_release)"; [ -n "$PREV" ] || { echo "No previous release to roll back to." >&2; exit 1; }
  echo "Rolling back code: $CUR -> $PREV (database is NOT changed; see header)"
  run ln -sfn "$PREV" "$PREFIX/current.new"; run mv -T "$PREFIX/current.new" "$PREFIX/current"
  run systemctl restart voltora-api
  [ "$DRY" = 1 ] || "$SRC/scripts/healthcheck.sh"
  exit 0
fi

echo "1/5 pre-upgrade backup (encrypted) + restore drill"
BK='set -a; . '"$ENVF"'; set +a; cd '"$CUR"'/backend && sudo -E -u voltora .venv/bin/python -m app.modules.ops.backup'
printf '+ %s create --encrypt && verify\n' "$BK"
if [ "$DRY" = 0 ]; then bash -c "$BK create --encrypt" && bash -c "$BK verify"; fi

echo "2/5 install new release (re-runs install-ubuntu.sh steps 6-8; configuration and database are kept)"
OLD="$CUR"
if [ "$DRY" = 1 ]; then "$SRC/scripts/install-ubuntu.sh" --dry-run | sed -n '/6\/9/,$p' | head -40; exit 0; fi
if ! "$SRC/scripts/install-ubuntu.sh"; then
  echo "UPGRADE FAILED: reverting code to $OLD" >&2
  ln -sfn "$OLD" "$PREFIX/current.new"; mv -T "$PREFIX/current.new" "$PREFIX/current"; systemctl restart voltora-api
  "$SRC/scripts/healthcheck.sh" || echo "Service still unhealthy after revert: inspect 'journalctl -u voltora-api -n 100'" >&2
  exit 1
fi
echo "3/5 health check"; "$SRC/scripts/healthcheck.sh"
echo "4/5 keep the 5 newest releases"
ls -1dt "$PREFIX"/releases/*/ | tail -n +6 | xargs -r rm -rf
echo "5/5 done. Previous release kept for: scripts/upgrade-ubuntu.sh --rollback"
