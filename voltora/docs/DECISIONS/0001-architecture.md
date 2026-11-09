# 0001 — Modular monolith: FastAPI + PostgreSQL + Vite React

Status: accepted · Date: 2026-11-09 · Supersedes: legacy FastAPI + MongoDB + CRA

## Context

Legacy VOLTORA is a single-tenant FastAPI app on MongoDB with no multi-document transactions (`legacy_backend.md` §10), string ids, ISO-string dates, invariants held by conditional single-document updates, and a CRA frontend that does not build from the shipped archive (`baseline_run.md` §5). Its defects cluster where money and stock cross collections without a transaction (P-03, P-04, L-3…L-9, L-16…L-19). The target deployment is one VPS operated by a merchant, with no ops team.

## Decision

* **One deployable**: a modular monolith. `backend/app/modules/<m>/{models,service,tools,api}.py` are auto-discovered (`app.main.discover_modules`); no central registry to edit. Cross-module calls go through service functions and the event outbox (ADR 0003), never through another module's tables.
* **PostgreSQL 16** as the only datastore: ACID transactions for orders, stock and refunds, CHECK/UNIQUE constraints as the last line of defence, JSONB where documents are the right shape (builder documents, settings values, attributes), `pg_dump` for backups.
* **FastAPI + SQLAlchemy 2 + Alembic**; migrations are the only way schemas change in production (`alembic upgrade head` runs at install, upgrade and container start).
* **Vite + React 19 + strict TypeScript** SPA serving storefront, admin, studio and installer from one bundle, served by the API's static fallback or Caddy. Admin modules self-register (`modules/<m>/index.ts`), permission-gated.
* **One process** runs API + outbox worker + periodic jobs (`run_worker_in_process`). Horizontal scale is not a goal for v2.0; the seam is the outbox/job tables.

## Alternatives considered

| Option | Why not |
|---|---|
| **Next.js SSR** (storefront SEO, one language) | Would split the stack in two (Node for storefront, Python for everything the rebuild already specifies: AI tools, plugins, backups). SEO needs are met by the API serving per-route meta for crawlers and a prerender step later; revisit if measured SEO or LCP requires SSR. Cost avoided: second runtime on a small VPS |
| **Keep MongoDB** | Money/stock invariants would stay application-level; backups/restores lack a transactional snapshot; legacy used 4.4 (EOL) because of CPU limits (I-01). Migration cost is paid once by the importer (`docs/MIGRATION_MAP.md`) |
| **Microservices** | No team or scale to justify; every boundary becomes a network failure mode |
| **Django/ORM-admin** | Admin is a product surface (saved views, command search, permission-aware navigation), not a CRUD scaffold |

## Consequences / risks

* A bug in one module can take the process down: mitigated by "a module must stay importable at all times" (ENGINEERING.md) and per-module tests against real PostgreSQL.
* Single process = single point of failure and no zero-downtime deploy: `systemd`/compose restart in seconds; documented as a limit.
* Admin and storefront share a bundle: route-level code splitting keeps the storefront payload separate.
* Auto-discovery means a broken `models.py` breaks Alembic and tests for everyone; `tests/test_migrations.py` fails fast when metadata and migrations drift.

## Migration path

Legacy → new through `app.modules.migration` (dry-run, idempotent apply, reconciliation); the legacy system keeps running read-only until reconciliation and a staging smoke pass; rollback = point DNS back (data was copied, not moved). Details in `docs/MIGRATION_MAP.md` and `docs/DEPLOYMENT.md`.
