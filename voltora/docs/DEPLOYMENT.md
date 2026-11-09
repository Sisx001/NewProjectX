# Deployment

Two supported paths, same application, same Caddyfile:

| Path | Use when | Entry point |
|---|---|---|
| **A. Docker Compose** | VPS with Docker 24+ and the compose plugin | `deploy/docker-compose.yml` |
| **B. Ubuntu 24.04 bare metal** (no Docker, no nginx) | small VPS, systemd preferred | `scripts/install-ubuntu.sh` |

> **Verification status (read first).** In the environment where this was written there was **no Docker daemon, no Caddy binary and no VPS**. What *was* run: `bash -n` on every script, `scripts/install-ubuntu.sh --dry-run` (full action list), `make -n` for all targets, the `.env.example` generation commands (the generated secret/vault key pair boots `Settings` and round-trips `seal/unseal`), `alembic upgrade head` on an empty PostgreSQL 16 (+ schema-vs-models diff test), backup → restore drill, legacy import smoke test, and the local dev stack. **Not run:** `docker build`, `docker compose up`, `caddy validate`, the Ubuntu installer for real, ACME certificate issuance, `ufw`. Treat those as unverified until you have run them on a disposable VPS (`docs/OPERATIONS.md` §7 has the acceptance checklist).

## 0. Requirements

* 1 vCPU / 2 GB RAM minimum (the worker, API and PostgreSQL share it; restore drills briefly need extra disk equal to the database size), 20 GB disk plus your media.
* A DNS name pointing at the server (A/AAAA) *before* first start, ports 80 and 443 reachable (Let's Encrypt HTTP-01 / TLS-ALPN).
* Never expose PostgreSQL (5432) or the API port (8000): in both paths they are bound to a private network / loopback.

## 1. Configuration reference

All settings are environment variables with the `VOLTORA_` prefix (or `backend/.env`).

| Variable | Default | Notes |
|---|---|---|
| `VOLTORA_ENV` | `development` | `production` / `staging` enforce the guards below |
| `VOLTORA_DATABASE_URL` | `postgresql+psycopg://voltora@127.0.0.1:5433/voltora` | |
| `VOLTORA_SECRET_KEY` | dev value | **required ≥ 32 chars** outside development |
| `VOLTORA_VAULT_KEY` | empty | **required** outside development; Fernet key; seals credentials and derives backup keys (ADR 0007) |
| `VOLTORA_PUBLIC_URL` | `http://localhost:5173` | `https://` turns on `Secure` cookies |
| `VOLTORA_ALLOWED_ORIGINS` | localhost list | comma list; must include the public origin |
| `VOLTORA_DATA_DIR` | `.data` | media, backups, bootstrap token — **back it up / mount a volume** |
| `VOLTORA_TRUSTED_PROXIES` | `127.0.0.1` | exact addresses (not CIDR) whose `X-Forwarded-For` is honoured |
| `VOLTORA_PAYMENTS_MODE` | `sandbox` | `live` only after each provider is verified in the UI |
| `VOLTORA_EXTERNAL_NETWORK` | `false` | master switch for calls to real providers |
| `VOLTORA_AI_ALLOW_PRIVATE_ENDPOINTS` | `false` | SSRF guard for AI base URLs |
| `VOLTORA_SESSION_DAYS` | 14 | |
| `VOLTORA_RUN_WORKER_IN_PROCESS` | `true` | outbox worker + periodic jobs; keep one API process |
| `VOLTORA_LOG_LEVEL` | `INFO` | |
| `VOLTORA_AUTO_MIGRATE` | `1` (Docker entrypoint only) | set `0` to run `alembic upgrade head` yourself |

## 2. Path A — Docker Compose

```bash
git clone <repo> voltora && cd voltora
cp deploy/.env.example deploy/.env
# edit deploy/.env: SITE_ADDRESS, ACME_EMAIL, then generate and paste:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # VOLTORA_SECRET_KEY
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"   # VOLTORA_VAULT_KEY  (store a copy in a password manager)
docker compose -f deploy/docker-compose.yml config            # validate before starting
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f api       # wait for "Application startup complete"
docker compose -f deploy/docker-compose.yml exec api cat /data/bootstrap-token   # one-time setup token
```

What the stack does: `db` (postgres:16, healthcheck, named volume, **no published port**), `api` (image built by `deploy/Dockerfile`: Node build of `web/` → Python 3.12 slim with `uv sync --frozen`, PGDG `postgresql-client-16` for backups, non-root uid 10001, entrypoint runs `alembic upgrade head` then uvicorn), `caddy` (automatic HTTPS, security headers/CSP, reverse proxy to `api:8000`, fixed address `172.29.0.10` which is the only `VOLTORA_TRUSTED_PROXIES` entry). Data volumes: `pgdata`, `appdata` (`/data`: media, backups, token), `caddy_data` (certificates — keep it, ACME rate limits are real).

Local rehearsal without a domain: `SITE_ADDRESS=localhost` (Caddy issues a local self-signed certificate); open `https://localhost`.

## 3. Path B — Ubuntu 24.04 bare metal

```bash
sudo scripts/install-ubuntu.sh --dry-run --domain shop.example.com --email you@example.com --with-caddy   # review every action
sudo scripts/install-ubuntu.sh            --domain shop.example.com --email you@example.com --with-caddy
```

Layout: code in `/opt/voltora/releases/<timestamp>` with `/opt/voltora/current` symlink; config `/etc/voltora/voltora.env` (0640 root:voltora, created once with generated secrets); data `/var/lib/voltora`; logs `/var/log/voltora` (+ logrotate); systemd `voltora-api.service` (hardened: `NoNewPrivileges`, `ProtectSystem=strict`, write access only to data/log dirs) behind Caddy on 127.0.0.1:8000; PostgreSQL 16 from Ubuntu on loopback with a generated role password; `uv` is installed into a private venv (`pip install uv==0.9.28`, no `curl | sh`), Node (only for the web build) is downloaded from nodejs.org and checksum-verified. Re-running the script upgrades to the current checkout and keeps secrets, data and database.

Firewall and SSH are **never touched** unless you pass `--configure-ufw` (it allows the detected SSH port first, then 80/443, then enables ufw). Without the flag it prints the safe sequence (`ufw allow OpenSSH; ufw allow 80,443/tcp; ufw enable`) for you to run from a console-recoverable session. SSH keys and `sshd_config` are never modified.

## 4. First run and installation wizard

1. Start the stack. With no installation on record the API writes a **one-time bootstrap token** to `<data_dir>/bootstrap-token` (Docker: `/data/bootstrap-token`; bare metal: `/var/lib/voltora/bootstrap-token`) and logs where it is.
2. Open `https://<domain>/` → the wizard (`/api/install/*`): environment checks, store identity (currency BDT, timezone Asia/Dhaka), owner account (strong password, optional TOTP), optional catalog starter, **complete**. The token stops working on completion; there are no default credentials.
3. This replaces the legacy flow where the wizard was blocked at the edge (D-03): `/api/install/*` is proxied like every other API route and protected by the token and rate limiting, not by hiding it. If you prefer, restrict `/api/install/*` to your IP in the Caddyfile for the first hour.
4. After install: Settings → Backups (enable, set retention), run **Back up now** then **Verify by restoring** (System → Backups), and add a second off-server copy (see OPERATIONS §2).
5. Providers (payments, couriers, SMS) are pre-loaded in **sandbox / unverified** state. Nothing reaches a real provider until `VOLTORA_EXTERNAL_NETWORK=true`, `VOLTORA_PAYMENTS_MODE=live` *and* the provider's credentials have been verified in the admin UI.

## 5. TLS, headers, proxy

`deploy/Caddyfile` (one file for both paths; `SITE_ADDRESS`, `ACME_EMAIL`, `UPSTREAM` are variables): automatic HTTPS, HSTS, `nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, `Permissions-Policy`, COOP, `Server` header removed, long-cache for hashed `/assets/*`, 30 MB request cap, JSON access log, upstream health checks on `/api/health`, and a CSP compatible with the SPA (self-hosted scripts/fonts; `style-src 'unsafe-inline'` because React emits style attributes; `form-action` left unrestricted because some gateways return through a form POST). CAPTCHA or analytics origins are added with `CSP_SCRIPT_SRC_EXTRA`, `CSP_FRAME_SRC_EXTRA`, `CSP_CONNECT_SRC_EXTRA` (hCaptcha/Turnstile examples in `.env.example`) — this is the fix for W-01 and is untested against a real widget. Caddy overwrites client-supplied `X-Forwarded-*`; the API honours them only from `VOLTORA_TRUSTED_PROXIES`. Behind a CDN, add its ranges to Caddy's `trusted_proxies` and put Caddy's address in `VOLTORA_TRUSTED_PROXIES`.

## 6. Upgrade and rollback

| | Docker | Bare metal |
|---|---|---|
| Upgrade | `deploy/upgrade-docker.sh` | `sudo scripts/upgrade-ubuntu.sh` (`--dry-run` first) |
| Steps | **encrypted backup + restore drill** → build new image tag → start (entrypoint migrates) → wait for `healthy` → on failure revert to previous tag | encrypted backup + drill → new release dir → `uv sync --frozen` + web build → `alembic upgrade head` → switch symlink, restart → `scripts/healthcheck.sh` → on failure revert symlink |
| Code rollback | `deploy/upgrade-docker.sh --rollback <tag>` | `sudo scripts/upgrade-ubuntu.sh --rollback` |
| Schema rollback | **forward-only.** Restore the pre-upgrade backup into a *new* database and switch `VOLTORA_DATABASE_URL` (OPERATIONS §2). Alembic `downgrade` is not a supported production path | same |

Rules: never skip the backup step; migrations must be backward compatible with the previous release for one version where possible (add columns nullable/with defaults, drop in a later release) so a code-only rollback is safe.

## 7. Staging

Run a second instance with `VOLTORA_ENV=staging` (same guards as production), its own database and `SITE_ADDRESS` (`staging.example.com`), sandbox payments, and `VOLTORA_EXTERNAL_NETWORK=false`: Docker — `docker compose -p voltora-staging --env-file deploy/.env.staging -f deploy/docker-compose.yml up -d` (change the fixed subnet/address in a copy of the compose file if both run on one host, and the published ports are the only conflict — use a separate host or a different Caddy). Seed it from production with `backup create` → restore into the staging database (this copies customer data: apply your data-handling policy). Promote by running `upgrade` on staging first and executing the journeys in OPERATIONS §7.

## 8. Migrating from legacy VOLTORA

Follow `docs/MIGRATION_MAP.md` §1: install the new system, export (`scripts/export-legacy-mongo.sh`), dry-run, review `unmapped_*`/`not_migrated`, apply, check the reconciliation block, run the staging journeys, then switch DNS. The legacy system stays untouched (the exporter only reads), which is the rollback.

## 9. Security checklist before go-live

- [ ] `VOLTORA_ENV=production`, secrets generated (not the examples), vault key stored outside the server
- [ ] DNS + HTTPS working, HSTS present (`curl -I https://<domain>`)
- [ ] `5432` and `8000` unreachable from the internet (`nmap` from outside)
- [ ] Owner has TOTP enabled; `security.require_staff_mfa` considered
- [ ] Backup made, **restore drill passed**, off-server copy exists
- [ ] Payments still `sandbox` until providers are verified; external network deliberately enabled
- [ ] `bootstrap-token` consumed; no default accounts
