#!/usr/bin/env bash
# VOLTORA bare-metal installer for Ubuntu 24.04 (no Docker, no nginx): PostgreSQL 16 + uvicorn under systemd + Caddy (HTTPS).
# Idempotent: re-running upgrades the code to the current checkout and keeps secrets, data and the database.
#
#   sudo scripts/install-ubuntu.sh --dry-run --domain shop.example.com      # print every action, change nothing
#   sudo scripts/install-ubuntu.sh --domain shop.example.com --email you@example.com --with-caddy
#
# Options:
#   --domain NAME        public hostname (needed with --with-caddy; DNS A/AAAA must already point here)
#   --email ADDR         ACME/Let's Encrypt contact
#   --with-caddy         install Caddy from its official apt repository and configure HTTPS + security headers
#   --configure-ufw      OPT-IN firewall: allows the detected SSH port + 80/443 first, then enables ufw. Never run implicitly.
#   --node-version X.Y.Z Node.js used only to build the web app (downloaded from nodejs.org, checksum verified)
#   --dry-run            print actions only
# Never touches sshd or SSH keys. UNVERIFIED on a real VPS in the authoring environment (see docs/DEPLOYMENT.md).
set -euo pipefail

DRY=0; DOMAIN=""; EMAIL=""; WITH_CADDY=0; UFW=0; NODE_VERSION="24.22.2"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --domain) DOMAIN="$2"; shift ;;
    --email) EMAIL="$2"; shift ;;
    --with-caddy) WITH_CADDY=1 ;;
    --configure-ufw) UFW=1 ;;
    --node-version) NODE_VERSION="$2"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/voltora; ETC=/etc/voltora; DATA=/var/lib/voltora; LOGS=/var/log/voltora; ENVF="$ETC/voltora.env"
TS="$(date -u +%Y%m%dT%H%M%SZ)"; REL="$PREFIX/releases/$TS"

say() { printf '\n== %s\n' "$*"; }
run() { printf '+ %s\n' "$*"; if [ "$DRY" = 0 ]; then "$@"; fi; }
sh_() { printf '+ %s\n' "$*"; if [ "$DRY" = 0 ]; then bash -c "$*"; fi; }

if [ "$DRY" = 0 ] && [ "$(id -u)" != 0 ]; then echo "Run as root (sudo), or use --dry-run." >&2; exit 1; fi
if [ "$WITH_CADDY" = 1 ] && [ -z "$DOMAIN" ]; then echo "--with-caddy needs --domain" >&2; exit 2; fi
. /etc/os-release 2>/dev/null || true
if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
  echo "WARNING: tested target is Ubuntu 24.04; this is ${PRETTY_NAME:-unknown}." >&2
fi
echo "VOLTORA install: source=$SRC release=$REL dry_run=$DRY"

say "1/9 system packages"
run apt-get update
run apt-get install -y --no-install-recommends ca-certificates curl xz-utils rsync python3 python3-venv \
  postgresql postgresql-client logrotate openssl

say "2/9 service user and directories"
if ! id voltora >/dev/null 2>&1; then run useradd --system --home-dir "$PREFIX" --shell /usr/sbin/nologin voltora; fi
run install -d -o root -g root -m 0755 "$PREFIX" "$PREFIX/releases"
run install -d -o root -g voltora -m 0750 "$ETC"
run install -d -o voltora -g voltora -m 0750 "$DATA" "$LOGS"

say "3/9 PostgreSQL role and database (local socket/loopback only)"
if [ "$DRY" = 1 ] || ! sudo -u postgres psql -Atc "select 1 from pg_roles where rolname='voltora'" | grep -q 1; then
  DBPW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "+ CREATE ROLE voltora (password generated; stored only in $ENVF)"
  if [ "$DRY" = 0 ]; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE voltora LOGIN PASSWORD '$DBPW'" \
      -c "CREATE DATABASE voltora OWNER voltora ENCODING 'UTF8'"
    NEW_DBPW="$DBPW"
  fi
else
  echo "role voltora already exists; keeping it"
fi
echo "listen_addresses: $(sudo -u postgres psql -Atc 'show listen_addresses' 2>/dev/null || echo '(unknown in dry-run)') (must stay localhost; never expose 5432)"

say "4/9 configuration $ENVF (created once; never overwritten)"
if [ ! -f "$ENVF" ]; then
  if [ "$DRY" = 1 ]; then
    echo "+ write $ENVF with generated VOLTORA_SECRET_KEY, VOLTORA_VAULT_KEY, database URL (mode 0640 root:voltora)"
  else
    : "${NEW_DBPW:?database already existed but $ENVF is missing: create it from deploy/.env.example by hand}"
    umask 077
    SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    VAULT="$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
    PUBLIC="http://localhost"; [ -n "$DOMAIN" ] && PUBLIC="https://$DOMAIN"
    cat > "$ENVF" <<EOF
VOLTORA_ENV=production
VOLTORA_DATABASE_URL=postgresql+psycopg://voltora:${NEW_DBPW}@127.0.0.1:5432/voltora
VOLTORA_SECRET_KEY=${SECRET}
VOLTORA_VAULT_KEY=${VAULT}
VOLTORA_PUBLIC_URL=${PUBLIC}
VOLTORA_ALLOWED_ORIGINS=${PUBLIC}
VOLTORA_DATA_DIR=${DATA}
VOLTORA_TRUSTED_PROXIES=127.0.0.1
VOLTORA_PAYMENTS_MODE=sandbox
VOLTORA_EXTERNAL_NETWORK=false
EOF
    chown root:voltora "$ENVF"; chmod 0640 "$ENVF"
    echo "Created $ENVF. BACK UP VOLTORA_VAULT_KEY somewhere safe: without it sealed secrets and encrypted backups are unrecoverable."
  fi
else
  echo "$ENVF exists; keeping it"
fi

say "5/9 build tools (uv in a private venv; Node only for the web build)"
run python3 -m venv "$PREFIX/tools"
run "$PREFIX/tools/bin/pip" install --quiet --upgrade "uv==0.9.28"
if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -lt 22 ]; then
  NODE_TGZ="node-v${NODE_VERSION}-linux-x64.tar.xz"
  sh_ "cd /tmp && curl -fsSLO https://nodejs.org/dist/v${NODE_VERSION}/${NODE_TGZ} && curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt | grep ' ${NODE_TGZ}\$' | sha256sum -c - && install -d $PREFIX/node && tar -xJf ${NODE_TGZ} -C $PREFIX/node --strip-components=1 && rm -f ${NODE_TGZ}"
  NODE_BIN="$PREFIX/node/bin"
else
  NODE_BIN="$(dirname "$(command -v node)")"
fi

say "6/9 release $REL"
run install -d -o root -g root -m 0755 "$REL"
run rsync -a --delete --exclude node_modules --exclude .venv --exclude .git --exclude .data --exclude 'web/dist' --exclude __pycache__ "$SRC/" "$REL/"
sh_ "cd $REL/backend && UV_PROJECT_ENVIRONMENT=$REL/backend/.venv $PREFIX/tools/bin/uv sync --frozen --no-dev"
sh_ "cd $REL/web && PATH=$NODE_BIN:\$PATH npm ci --no-audit --no-fund && PATH=$NODE_BIN:\$PATH npm run build"
run chown -R root:voltora "$REL"
run chmod -R g+rX,o-rwx "$REL"

say "7/9 database migrations (alembic upgrade head)"
sh_ "cd $REL/backend && set -a && . $ENVF && set +a && sudo -E -u voltora $REL/backend/.venv/bin/alembic upgrade head"

say "8/9 activate release, install systemd units and log rotation"
PREV="$(readlink -f "$PREFIX/current" 2>/dev/null || true)"
[ -n "$PREV" ] && echo "previous release: $PREV (for rollback: scripts/upgrade-ubuntu.sh --rollback)"
run ln -sfn "$REL" "$PREFIX/current.new"
run mv -T "$PREFIX/current.new" "$PREFIX/current"
run install -m 0644 "$REL/deploy/systemd/voltora-api.service" /etc/systemd/system/voltora-api.service
run install -m 0644 "$REL/deploy/systemd/voltora-backup.service" /etc/systemd/system/voltora-backup.service
run install -m 0644 "$REL/deploy/systemd/voltora-backup.timer" /etc/systemd/system/voltora-backup.timer
run install -m 0644 "$REL/deploy/logrotate/voltora" /etc/logrotate.d/voltora
run systemctl daemon-reload
run systemctl enable voltora-api
run systemctl restart voltora-api

if [ "$WITH_CADDY" = 1 ]; then
  say "8b/9 Caddy (official apt repository) with HTTPS for $DOMAIN"
  sh_ "apt-get install -y --no-install-recommends debian-keyring debian-archive-keyring apt-transport-https gnupg"
  sh_ "curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
  sh_ "curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o /etc/apt/sources.list.d/caddy-stable.list"
  run apt-get update
  run apt-get install -y caddy
  run install -m 0644 "$REL/deploy/Caddyfile" /etc/caddy/Caddyfile
  run install -d /etc/systemd/system/caddy.service.d
  sh_ "printf '[Service]\nEnvironment=SITE_ADDRESS=%s\nEnvironment=ACME_EMAIL=%s\nEnvironment=UPSTREAM=127.0.0.1:8000\n' '$DOMAIN' '$EMAIL' > /etc/systemd/system/caddy.service.d/voltora.conf"
  run systemctl daemon-reload
  sh_ "caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile"
  run systemctl enable --now caddy
  run systemctl reload-or-restart caddy
fi

say "9/9 firewall"
if [ "$UFW" = 1 ]; then
  SSHPORT="$(ss -H -tlnp 2>/dev/null | awk '/sshd/ {n=split($4,a,":"); print a[n]; exit}')"
  : "${SSHPORT:=22}"
  echo "Allowing SSH on port $SSHPORT BEFORE enabling the firewall."
  run apt-get install -y ufw
  run ufw allow "${SSHPORT}/tcp"
  run ufw allow 80/tcp
  run ufw allow 443/tcp
  run ufw --force enable
else
  cat <<'EOF'
Firewall NOT changed (opt-in only). Safe sequence when you are ready, from a session you can recover (console access!):
    sudo ufw allow OpenSSH        # or your custom SSH port
    sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
    sudo ufw enable
PostgreSQL (5432) and the API (8000) must stay unreachable from outside; both bind to 127.0.0.1.
EOF
fi

say "health check"
if [ "$DRY" = 1 ]; then echo "+ scripts/healthcheck.sh http://127.0.0.1:8000/api/health"; else "$SRC/scripts/healthcheck.sh" http://127.0.0.1:8000/api/health 30; fi

cat <<EOF

Done. First-run setup: the one-time bootstrap token is in $DATA/bootstrap-token (root/voltora readable only).
Open ${DOMAIN:+https://$DOMAIN}${DOMAIN:-http://<server>:8000 through an SSH tunnel} and complete the installation wizard; then delete nothing by hand: the token is invalidated on completion.
Backups: enable "Scheduled backups" in Settings → Backups (in-app) or: systemctl enable --now voltora-backup.timer (not both). Keep an off-server copy.
EOF
