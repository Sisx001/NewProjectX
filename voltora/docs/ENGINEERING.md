# VOLTORA engineering conventions

Read this before changing code. It is the contract that lets several engineers (and agents) work in parallel.

## Layout

```
voltora/
  backend/            FastAPI modular monolith (Python 3.12, uv)
    app/core/         config-independent primitives: errors, security, rbac, deps, services (audit/outbox/settings/rate limit), ssrf
    app/modules/<m>/  one package per domain: models.py, service.py, tools.py (AI tools), api.py
    app/seed.py       seed hook registry (foundation data; demo data only when asked, flagged is_demo)
    tests/            pytest; real PostgreSQL
  web/                Vite + React 19 + strict TypeScript
    src/ui/           design system primitives (tokens.css, base.css, index.tsx, DataTable.tsx)
    src/admin/        admin shell + modules/<m>/index.ts auto-discovered
    src/store/        storefront app
    src/builder/      component registry, renderer, studio
  docs/  registry/  deploy/  scripts/
```

## Backend rules

* **Module discovery** — `app/main.py` imports `app.modules.<name>.{models,service,tools,api}` automatically. Expose `router` (and optional `extra_routers`, `MODULE = {"title", "description"}`) from `api.py`. Never edit `main.py` to register a module. A module must stay importable at all times — half-written code breaks every other module's tests.
* **Money** is `BigInteger` minor units (`*_minor`) + ISO currency. Never floats. Totals are computed server-side only.
* **Permissions** live in `app/core/rbac.py` (`PERMISSIONS`). Use `Depends(require("orders.write"))`; customers use `Depends(customer)`; public endpoints use `Depends(principal)` or `store_dep`. Every store-scoped query filters by `p.store.id` (object-level authorization). Add new permission keys to `PERMISSIONS` only when no existing key fits (coordinate: append, never rename).
* **Errors**: raise `AppError(code, message, status=…)`, `NotFound`, `Forbidden`, `Conflict`. Response shape `{"error": {"code","message","details"}}`.
* **Audit** every staff/AI/system mutation: `audit(db, store_id, p.actor, "orders.cancelled", "order", order.id, {...})` inside the same transaction. Secrets are redacted automatically by key name, but never pass raw secrets.
* **Events**: declare with `EV_X = event_type("order.placed", "description")` and emit with `emit(db, store_id, EV_X, payload)` in the same transaction as the state change. Subscribe with `@on("order.placed")`. Handlers must be idempotent (they may be retried).
* **Settings**: declare once with `setting(key=..., type=..., default=..., label=..., group=...)` in the owning module. Duplicate keys raise at import. Read with `get_setting(db, store_id, key)`. Never create a second settings page for the same concept.
* **Secrets**: store with `SecretRecord` + `seal()`; return only `mask()` hints. Never put secrets in events, audit data, logs, AI context or API responses.
* **Idempotency**: money-moving and order-creating endpoints accept an idempotency key and use `idempotent_lookup/idempotent_store` or a unique column.
* **Stock** changes only via `app/modules/inventory/ledger.py::apply_movement` (row-locked, writes `stock_movements`).
* **Periodic jobs**: `@periodic("name", every_seconds=…)` (runs in the worker thread; must be idempotent).
* **Admin shell hooks**: append to `NAV_COUNTERS` (actionable queue counts only) and `SEARCHERS` (command-palette record search; enforce permissions inside).
* **Seed**: `@seed_hook(order, "name")` functions must be idempotent; demo records must set `is_demo=True` (or equivalent) and be removable.
* **External providers** (payments, couriers, SMS, AI): never call real endpoints unless `settings.external_network` is true and credentials are verified. Sandbox adapters must be clearly labelled as simulations. Never claim an integration works without a contract test against documented request/response shapes (use `respx`).

## Tests

* `cd backend && VOLTORA_TEST_DB=voltora_test_<x> uv run pytest -q` — each parallel workstream uses its own database (`voltora_test_a` … `_f` exist).
* Fixtures (`tests/conftest.py`): `api` (CSRF-aware client), `new_api()`, `store` (seeded foundation), `make_staff(role_key)`, `owner_api`, `staff_api(new_api, make_staff, role)`, `login()`, `db`.
* Write tests for: happy path, permission denial, validation errors, idempotency/replay, concurrency where money or stock is involved.

## Web rules

* Admin modules: `web/src/admin/modules/<m>/index.ts` default-exports `AdminModule` (nav items, lazy routes, commands). The shell filters nav by permission; routes are permission-gated.
* Use primitives from `@/ui` (`Button`, `Field`, `TextField`, `Select`, `Dialog`, `Drawer`, `Confirm`, `Tabs`, `Card`, `Badge`, `StatusBadge`, `Alert`, `Empty`, `Skeleton`, `useToast`) and `@/ui/DataTable` (`DataTable`, `SavedViews`). **Do not edit `src/ui/index.tsx`, `base.css`, `tokens.css`**; add new shared primitives as new files in `src/ui/`.
* Data: `@tanstack/react-query` + `@/lib/api` (`get/post/put/patch/del`, CSRF handled). Money via `@/lib/money` (`formatMoney`, `parseMoney`).
* Every screen handles loading, empty, error and success states. No fake numbers, no placeholder charts, no dead buttons: if a capability needs configuration, say what is missing.
* Design language: warm paper + ink, one signal colour (`--v-signal`) used sparingly for current state/primary emphasis; Instrument Sans UI, Instrument Serif for editorial display, JetBrains Mono for identifiers. No decorative gradients/glows. Motion = short transform/opacity transitions; respect reduced motion.
* Accessibility: labelled controls, keyboard paths, focus visible, dialogs trap focus, touch targets ≥ 36px (44px on storefront).
* Checks: `cd web && npx tsc -p . --noEmit && npx vitest run && npx vite build`.

## Feature ledger

Each workstream records its features in `registry/features.d/<domain>.json` (array of objects with `id, owner_domain, title, dependencies, state, source_files, tests, visual_evidence, API_contract, migration, remaining_risks, last_verified`). States: `legacy_implemented`, `legacy_broken`, `planned`, `developed_unverified`, `staging_verified`, `production_verified`. Only mark `staging_verified` when automated tests and (for UI) a real browser check passed in this environment.
