# VOLTORA

A Bangladesh-first commerce operating system: storefront, merchant command center, visual builder, AI assistants and an extension platform in **one self-hostable application** (FastAPI + PostgreSQL + a Vite/React SPA). It is the ground-up rebuild of the legacy FastAPI + MongoDB + CRA VOLTORA; legacy data is carried over by a tested importer.

Status: **alpha (`2.0.0-alpha.1`)**. The feature ledger (`registry/features.json`, rendered in `docs/FEATURE_MATRIX.md`) says exactly what is implemented and what has passed real tests; nothing is `production_verified`. Pre-loaded providers (payments, couriers, SMS) are *sandbox / unverified* until you verify credentials in the admin UI.

## Quick start (development)

Requirements: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 22+ (24 tested), PostgreSQL 16 client/server binaries (`/usr/lib/postgresql/16/bin`). Run as a normal user (PostgreSQL refuses to start as root).

```bash
make install-dev          # uv sync + npm ci
make dev                  # local PostgreSQL on :5433 → alembic upgrade head → API :8000 + web :5173
```

Or step by step (the first three commands were run while preparing this README; `npm run dev` and `make dev` as a whole were not):

```bash
scripts/dev-db.sh                                             # PostgreSQL on 127.0.0.1:5433 (db "voltora")
cd backend && uv run alembic upgrade head                     # create the schema
uv run uvicorn app.main:app --port 8000                       # API + serves web/dist when built; GET /api/health → {"status":"ok"}
cd ../web && npm run dev                                      # http://127.0.0.1:5173 (proxies /api to :8000)
```

First run: the API prints/writes a one-time setup token (`backend/.data/bootstrap-token` when started from `backend/`); open the app and complete the installation wizard — there are no default credentials.

Tests and checks:

```bash
cd backend && VOLTORA_TEST_DB=voltora_test_a uv run pytest -q        # real PostgreSQL, one database per parallel run
cd backend && uv run ruff check .
cd web && npx tsc -p . --noEmit && npx vitest run && npx vite build
make check-scripts                                                   # bash -n over scripts/ and deploy/
```

## Architecture in one page

* **Modular monolith** — `backend/app/modules/<name>/{models,service,tools,api}.py`, auto-discovered; modules: `platform` (stores, settings registry, audit chain, vault, outbox, installer, system console), `identity`, `catalog`, `inventory`, `customers`, `commerce`, `payments`, `shipping`, `discounts`, `content` (builder documents/themes), `messaging`, `ai`, `plugins`, `automation`, `webhooks`, `ops` (backups, vault rotation), `migration` (legacy import). Conventions: `docs/ENGINEERING.md`; API contracts: `docs/API_CONTRACTS.md`.
* **Data** — PostgreSQL 16; money as integer minor units; stock only through a locked ledger; domain events through a transactional outbox; schema through Alembic.
* **Frontend** — `web/src/ui` design system (tokens, primitives), `web/src/admin` (modules self-register, permission-gated), `web/src/store` (storefront driven by theme tokens), `web/src/builder` (component registry, renderer, studio).
* **Security** — server-side sessions + CSRF, argon2, TOTP, RBAC permissions as the only authority (navigation, AI tools and plugins reference them), sealed secrets with a dedicated vault key, hash-chained audit trail, SSRF guard, rate limits, production start guards.
* **Operations** — `System` console (health, jobs, events, logs, audit, backups), encrypted backups with mandatory restore drills, runbooks.

Decisions with alternatives and risks: `docs/DECISIONS/0001`–`0007`. Design language: `docs/DESIGN_SYSTEM.md`.

## Deploy

| | |
|---|---|
| Docker Compose (postgres + app + Caddy HTTPS) | `deploy/docker-compose.yml`, `deploy/.env.example` |
| Ubuntu 24.04 without Docker | `scripts/install-ubuntu.sh --dry-run --domain shop.example.com --with-caddy` |
| Upgrade / rollback | `deploy/upgrade-docker.sh`, `scripts/upgrade-ubuntu.sh` |

**Docker/Caddy/VPS paths have not been executed in the authoring environment** — see the verification status block in `docs/DEPLOYMENT.md` and the acceptance checklist in `docs/OPERATIONS.md` §7.

## Backups and migration

```bash
cd backend
uv run python -m app.modules.ops.backup create --encrypt     # pg_dump + media, AES-256-GCM
uv run python -m app.modules.ops.backup verify               # restore drill into a scratch DB
uv run python -m app.modules.migration import --source /secure/legacy-export          # dry-run (default)
```

Legacy export, mapping and the smoke-test results: `docs/MIGRATION_MAP.md`. Vault key rotation: `docs/OPERATIONS.md` §5.

## Documentation map

`docs/BASELINE_AUDIT.md` (what legacy was, defect table with rebuild status) · `docs/MIGRATION_MAP.md` · `docs/DEPLOYMENT.md` · `docs/OPERATIONS.md` · `docs/DESIGN_SYSTEM.md` · `docs/ENGINEERING.md` · `docs/API_CONTRACTS.md` · `docs/FEATURE_MATRIX.md` · `docs/baseline/*` (raw legacy evidence).
