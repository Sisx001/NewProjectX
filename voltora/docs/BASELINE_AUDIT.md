# Baseline audit of legacy VOLTORA

Synthesised from `docs/baseline/{baseline_run,legacy_backend,legacy_frontend}.md`, the prior A–Z audit (`VOLTORA_AZ_AUDIT_2026-11-09.md`, static, nothing executed) and the endpoint inventory (`VOLTORA_endpoint_inventory.json`, AST-derived). Line references are `path:line` inside the legacy source (`/tmp/handoff/src`). "Rebuild status" is deliberately conservative: **addressed by design … (pending verification)** means the new architecture removes the cause but no test has yet proved it; **verified** names the evidence.

## 1. What the legacy system is

React 19 CRA + FastAPI + MongoDB (Motor), single tenant, ~236 Python files and a 43-suite Jest frontend.

| Area | Evidence |
|---|---|
| Backend | 346 route decorators (139 GET, 133 POST, 43 PUT, 29 DELETE, 2 PATCH) across the production app and the separate installer app; ~85 distinct Mongo collections (`<prefix><name>`, string ids, ISO-string timestamps) |
| Modules | `server, core, auth(+ext), permissions/roles (≈130 permissions), accounts, catalog, commerce, offers, giftcards, affiliates, payments + providers/{payments,couriers}, couriers, purchasing, cms/release_engine/themes (39 presets), ai/ai_tools/ai_monitor, support*, notifications/email, media, installer, seed` (`legacy_backend.md` §1) |
| Payments | bKash, SSLCommerz, Nagad, Rocket, Upay, Stripe, PayPal, bank transfer, COD (`legacy_backend.md` §4) |
| Couriers | Pathao, RedX, Steadfast, all `supports_webhook=False` (§5) |
| Frontend | 1 CRA SPA: storefront, admin (`pages/admin/*`), builder, installer; 46 shadcn files of which one is imported |
| Deploy | `deploy/` Caddy + compose (mongo:4.4, redis unused, api, web) + 4 shell scripts |

## 2. Runtime baseline (executed 2026-11-09, `docs/baseline/baseline_run.md`)

| Check | Result |
|---|---|
| Backend deps | installs from `requirements-deploy.txt`; `filelock` missing from requirements |
| MongoDB 7.0.14 (real binary) | started; API boots <10 s; `/api/health` ok; DB auto-seeds demo data |
| Backend pytest (147 files) | Run B: **973 passed, 10 skipped, 5 errors**; Run A: 1 failed (state-dependent `settings()` on unseeded DB). The 5 e2e errors (`403 Invalid owner setup key`) persisted with identical keys; root cause **not found** |
| e2e alone | 3 passed / 2 failed: `Secure` cookie flag absent over `http://`; checkout `400 "This delivery option does not cover the selected address"` with the test's address shape |
| Frontend install | `npm install` fails (ERESOLVE react-day-picker vs date-fns); two vendor tarball deps removed in the copy to proceed |
| `tsc --noEmit` | 50 errors, **0 in `src/`** (all `@types/node` vs TS 4.9.5) |
| `craco build` | fails: `craco.config.js` missing from the archive; then ajv hoisting; then ESLint plugin mismatch; builds only with `DISABLE_ESLINT_PLUGIN=true` plus a stand-in craco config (376 kB main.js) |
| Jest | 43 suites / 283 tests pass (stand-in config) |
| Not done | admin UI/MFA login in a browser, payments/couriers/AI (disabled), itemising the 10 skips |

Rebuild-time smoke (this team, same session): the legacy API was booted on the real mongod, a real guest checkout (`POST /api/checkout`, Idempotency-Key ≥16 chars, `location.district_id`) produced orders `VT-2A90733D`/`VT-60E00063`, then the export script and importer ran against that data (see `docs/MIGRATION_MAP.md` §7). The checkout failure above is therefore an address-shape problem of the test, not a general defect: with `location{division_id,district_id}` and a matching zone it succeeds.

## 3. Critical defects and rebuild status

IDs: **D/P/I/W/F/S** = prior audit; **L-n** = `legacy_backend.md` §11 item n. Owner modules are the new modular-monolith packages (`backend/app/modules/<m>`).

### 3.1 Deployment and operations

| ID | Sev | Defect (evidence) | Rebuild status |
|---|---|---|---|
| D-01 | Blocker | Web image expects an unshipped `frontend/build` (`Dockerfile.web.prebuilt:3`) | Addressed by design: multi-stage `deploy/Dockerfile` builds `web/` from `package-lock.json` (`npm ci`) and serves it from the API. Image build **unverified** (no Docker here). Bare-metal path builds web in `scripts/install-ubuntu.sh` — unverified on a VPS |
| D-02 | Blocker | README references absent `.env.example`/`Dockerfile.web` | Addressed: `deploy/.env.example` (generation commands validated: the generated key pair boots `Settings` and round-trips `seal/unseal`), `docs/DEPLOYMENT.md` |
| D-03 | Blocker | Install wizard calls `/api/install/*`, Caddy returns 404 for it (`Caddyfile:69-76`) | Addressed by design: installer lives inside the single app (`platform/install.py`), protected by a one-time bootstrap token written to `<data_dir>/bootstrap-token`, not by hiding the route. Caddyfile proxies all of `/api`. Pending verification of the full wizard journey (platform owner) |
| I-01 | High | Unsupported `mongo:4.4` | Eliminated: PostgreSQL 16 (`postgres:16-bookworm`) |
| I-02 | High | Backup default DB name contradicts generated DB (`backup-mongo.sh:14`, `installer.py:215`) | **Verified**: `ops.backup` dumps the database named in the *configured* `VOLTORA_DATABASE_URL`, never a default; `tests/test_backup.py` (backup → restore drill) passes; CLI smoke on a scratch DB verified |
| I-03 | Med | Caddy host hardcoded | Addressed: `{$SITE_ADDRESS}` templated; `caddy validate` **not run** (no Caddy) |
| I-04 | Med | `ufail2ban` typo | n/a: installer installs no fail2ban; firewall is opt-in and prints guidance |
| I-05 | Med | Unencrypted on-box backups, no offsite | Partly addressed: AES-256-GCM option (verified round-trip, tamper, wrong key, truncation), retention, mandatory restore drill. Off-server copy is a documented operator step, not automated |
| I-06 | Med | Watchdog coupled to another project's Telegram path | Not ported: health/jobs/log visibility is in-app (System console); external uptime monitoring documented in `docs/OPERATIONS.md` |
| W-01 | High | CAPTCHA blocked by CSP (`Caddyfile:185`, `server.py:119-123`) | Addressed by design: CSP origins extendable via `CSP_*_EXTRA` env; provider examples in `.env.example`. Unverified against a real widget |
| S-01 | Med | Frontend not reproducible (no lockfile) | Addressed: `web/package-lock.json` (lockfileVersion 3) and `backend/uv.lock`, `npm ci` / `uv sync --frozen` in all build paths |
| S-02 | Med | Vendor tarballs/wheel URLs | **Verified** by grep: no `emergent` reference in `web/package.json` or `backend/pyproject.toml` |
| S-03 | Low/Med | No licence file | Open: needs owner decision |
| F-01 | Med | Media 100 items/50 MB, stored in Mongo (`media.py:44-50`, L-41) | Addressed by design: media on disk/object-storage seam (content module); backups include `data_dir/media` (verified in test) |
| F-02 | Med | Mailbox send disabled | Open/by decision: messaging module owns real outbound mail; no draft-only promise |
| F-03/F-04 | Med | Demo route always 404; demo products seeded on empty catalog | Addressed by design: seeds create foundation only; demo data only on request and flagged `is_demo` |
| L-39 | Low | Mongo without auth, Redis unused | Eliminated; the compose DB has no published port |
| L-40 | Low | Rate-limit counters in Mongo on every write, IP-only | Addressed by design: `core.services.RateLimiter` (pending verification, platform owner) |

### 3.2 Payments, refunds, money

| ID | Sev | Defect | Rebuild status |
|---|---|---|---|
| P-01 | High | Stripe webhook adapter signature mismatch (`payments.py:174`, `stripe.py:137`) | Addressed by design: single provider contract + `respx` contract tests (payments owner). Pending verification |
| P-02 | High | bKash `trxID` not persisted for refunds | Same; durable `payment` records (payments owner). Pending |
| P-03 | High | Refund sent before durable idempotent ledger (`payments.py:238-270`) | Addressed by design: refund intents/ledger first, then provider call (ADR 0002). Pending |
| P-04 | High | Item refund ignores conditional-update result | Addressed by design: refunds are rows with constraints (`refunded_minor <= paid_minor` CHECK exists on `orders`) in one DB transaction. Pending |
| L-1 | High | Verify trusts client-supplied provider ids; no order/amount binding | Addressed by design: server-side verification bound to payment record (payments owner). Pending |
| L-2 | High | PayPal never captures | Addressed by design; unverified against sandbox |
| L-3 | High | Webhook dedupe recorded before processing | Addressed by design: outbox + idempotent handlers (ADR 0003). Pending |
| L-4…L-9 | Med | Refund accounting, overwrite vs `$inc`, ignored `ok`, whole-order `refunded`, missing amount/currency checks, swallowed side-effect errors | Addressed by design (integer ledgers, row locks, outbox retries). Pending |
| L-11…L-14 | Med/Low | Courier unit/host/status mistakes, dead courier webhooks | Shipping owner. Pending |
| L-15 | Low | No BD phone normalization | **Verified in importer**: `normalize_phone` → `01XXXXXXXXX` (unit-tested); runtime normalization is catalog/customers/commerce |
| L-16, L-17 | Med | Coupon/wallet not restored on cancel | `discount_redemptions.status=released` model exists; behaviour pending (discounts/commerce owners) |
| L-18, L-19 | Med | `initiating` never reaped; random 32-bit non-unique order number | `orders.number` UNIQUE + DB sequence (model present); importer drops hidden `initiating` orders and reports them. Pending runtime verification |
| L-21, L-22 | Low | Gift-card tax base, `min_spend` on pre-tier subtotal, no upper bound on percentage coupon | Importer rejects percentage > 100 (verified in test); runtime rules pending |

### 3.3 Security and auth

| ID | Sev | Defect | Rebuild status |
|---|---|---|---|
| L-35 | Med | Lockout per target email enables owner-lockout DoS | Addressed by design (per-IP + per-account throttling, identity module). Pending |
| L-36 | Med | AI `base_url` SSRF, key exfiltration, approve race | `core/ssrf.py`, AI approval states (ai owner). Pending |
| L-37 | Med | Single Fernet key from `APP_SECRET`, no rotation | Addressed by design: dedicated `VOLTORA_VAULT_KEY`, startup refuses without it in staging/production; rotation runbook in `docs/OPERATIONS.md` (ADR 0007). Re-sealing tool is a **gap** (see OPERATIONS §5) |
| L-38 | Low | Audit logs expire after 365 d | Addressed: append-only hash-chained `audit_events` (no TTL); chain verification is part of every restore drill (verified) |
| L-41 | Low | Blobs in Mongo | See F-01 |
| L-42 | Low | Idempotency replay re-calls provider | Idempotency records store response (payments owner). Pending |

### 3.4 Defects not in the prior audit that affect the migration (L-5x)

| Defect | Handling |
|---|---|
| Order links (`access`) are an HMAC of `order.id`+`idempotency_key` with `APP_SECRET`, not stored | Cannot be honoured: imported orders receive a random unusable `access_token_hash`; customers use number + phone tracking |
| MFA secrets sealed with `Fernet(sha256(APP_SECRET))` | Re-sealed with the new vault when `VOLTORA_LEGACY_APP_SECRET` is supplied; otherwise users re-enrol (reported) |
| Phone stored raw, unique on raw string | Normalized; duplicates by raw string become one phone |
| Variant/SKU ids globally unique strings | Kept in `legacy_id`; SKU clash with existing data is reported as invalid, never merged |
| Single `inventory{id:'main'}` document nets out reservations | Reconstructed: `on_hand = legacy + open reservations`, `reserved = open reservations` |
| Hidden `initiating`/`failed` order statuses | `initiating` dropped (reported), `failed` → `cancelled` with reason |

## 4. Duplicate and contradiction maps (legacy → single home in the rebuild)

| Concept | Legacy duplicates (evidence `legacy_frontend.md` §3) | Rebuild home |
|---|---|---|
| Navigation / footer | Settings `navigation`/`footer_links` **and** builder `site.header`/`site.footer` | Content module: builder document model only (ADR 0005) |
| Auth page styling | Settings "Login/signup page" **and** builder `site.auth.*` | Builder |
| Brand / theme | Settings "Brand & identity", builder theme panel, ThemeGallery | One theme token set (`--t-*`) edited in the builder; store identity in `store.*` settings |
| Providers / keys | Settings `payments`, `ServiceKeys`, `Connections`, `Couriers`, Operations providers, `TransactionsLog` | Payments/shipping provider registry + vault `SecretRecord`; verified vs pre-loaded state is explicit |
| Currency / language | `MarketSettings` vs Settings localization | One settings registry group |
| Inventory adjust / loyalty adjust / shipments | Same endpoint from two pages | One endpoint each (`inventory.ledger.apply_movement`) |
| Discounts | coupons, offers, gift-cards, loyalty tiers, affiliate codes, bundle discounts (5 engines) | `discounts` module (single model); other engines are separate modules, not parallel checkouts |
| AI | `ai`, `ai-connection`, `MonyChat`, OwnerAI, `MiraChat`, `/assistant` (three names) | `ai` module: one provider registry, specialised assistants |
| Feature toggles | Settings `features` vs `site.checkout.show_coupon` | Installed modules/plugins and settings registry |
| Settings pages | 6 nav entries over `StoreSettings` | One canonical `Settings` page generated from the server registry (`setting(...)`, duplicate keys raise at import) |
| Offers checkout | `offers.py` has its own checkout path | Not ported; single checkout |

Contradictions the rebuild removes: `SiteCheckout.fields` toggles fields the server still requires (L-32); `Settings.demo_catalog` forced true outside production (L-30); same `workspace_path` default for every install (L-30); AI connection stored under id `deepseek` for every provider (L-31); hard-coded "1 / 4" setup counter, constant "Sandbox" pill and "Isolated environment" footer; `mock` gateway in the live UI; Google Fonts loaded in addition to bundled fonts; production domain hard-coded in Caddyfile and `index.html`.

## 5. Gaps that remain open (honest list)

* Docker image, compose stack, Caddyfile and the Ubuntu installer are **syntactically checked only** (`bash -n`, dry-run output); none has been executed against Docker, Caddy or a VPS.
* No external load/soak test; the backup path was exercised on a 946-row database.
* Legacy collections without importer: reviews, support threads, gift cards, affiliates, loyalty/credit ledgers, returns, themes/documents/releases, media binaries (see `docs/MIGRATION_MAP.md`).
* Licence/ownership of bundled assets (S-03) is undetermined.
