# Legacy → VOLTORA migration map

Source of truth for the legacy shapes: `docs/baseline/legacy_backend.md` §10 and the real export produced by `scripts/export-legacy-mongo.sh`. Status column: **imported** = implemented in `backend/app/modules/migration/importer.py` and covered by `tests/test_legacy_migration.py`; **runtime** = behaviour replaced by new code (no data to move); **manual** = operator re-creates it; **no importer** = documented gap.

## 1. Running a migration

```bash
# 1. On the legacy host (needs pymongo; the legacy backend venv has it). Output contains password hashes + PII: 0700/0600, delete afterwards.
LEGACY_PYTHON=/path/to/legacy/backend/.venv/bin/python \
  scripts/export-legacy-mongo.sh --uri "$MONGO_URL" --db "$DB_NAME" --prefix "${REBUILD_COLLECTION_PREFIX:-}" --out /secure/legacy-export

# 2. New installation must be installed first (a Store must exist). Dry run is the default and writes nothing:
cd backend && uv run python -m app.modules.migration import --source /secure/legacy-export --report /tmp/dry.json
# 3. Apply (optionally carry MFA enrolments: the legacy APP_SECRET is read from the environment, never argv)
VOLTORA_LEGACY_APP_SECRET=... uv run python -m app.modules.migration import --source /secure/legacy-export --apply --report /tmp/apply.json
```

Properties (all tested): dry-run executes the same code inside a rolled-back transaction, so its counts/validation/reconciliation equal an apply; per-record savepoints (an invalid record is reported, never fatal); **insert-only and idempotent** through `legacy_id_map` — a re-run creates nothing and never overwrites, so re-running after go-live cannot reset live stock, prices or edited settings; targets whose module/table is absent are skipped with an explicit `skipped_targets` entry; unknown source fields are listed under `unmapped_fields`; collections deliberately not read are listed under `not_migrated` with the reason. Exit code 1 when any record is invalid.

## 2. Collections and fields

| Legacy collection → fields | New target | Status / rule |
|---|---|---|
| **users** `id,email,name,phone,password_hash,role,disabled,ban,mfa_*,addresses[],created_at` | `users` (+`memberships`, `customers`, `addresses`); `users.legacy_id` | **imported.** `role=='customer'` → `kind=customer` + `customers` row + addresses; any other role → `kind=staff`. argon2 hashes kept verbatim (verified: a migrated staff user logs in with the legacy password); non-argon2 hash → no password (reset required, reported). Existing email → linked, nothing overwritten. `disabled`/`ban.active` → `status=disabled`. `marketing_consent` is **never** set (legacy had no auditable consent). `mfa_secret` re-sealed with the new vault only if `VOLTORA_LEGACY_APP_SECRET` is supplied. Not migrated: `points`, `credit_balance`, `bag`, `grant` (per-user grants), `avatar` |
| role names (`owner, super_admin, admin, store_manager, moderator, product_manager, seller, order_manager, finance_manager, content_editor, support_agent, marketing_manager, inventory_manager, seo_manager, analyst, investor, designer`) | builtin roles `owner, store_admin, store_manager, support_agent, catalog_staff, fulfillment, finance, marketer, auditor` | **imported** (table `ROLE_MAP`). Custom role ids have no equivalent → user created **without access**, warning issued |
| **roles** (custom) | `roles` | **manual** (permission vocabularies differ: legacy ≈130 `x.update` verbs vs new `x.write`) |
| **sessions, login_attempts, login_history, otps, rate_limits, events, previews, email_queue, notifications** | – | not migrated (ephemeral; users sign in again) |
| **categories** `id,name,slug,description,image,enabled,parent_id,position,seo_title` | `categories` | **imported**, two passes for hierarchy; slug clash with a *different* legacy id → invalid; clash with an unmapped existing row → linked |
| **brands** `id,name,slug` | `brands` (id kept in `legacy_id_map`; table has no `legacy_id`) | **imported**. `products.brand` holds a *name* in real data (`"Auralis"`): resolved by id, then by slug, else a brand is auto-created |
| **collections** | – | no importer (storefront collections are builder/content concerns) |
| **products** `id,name,slug,sku,brand,category,description,price,compare_price,images[],image_alts,specs,variants[],badge,featured,preorder,tiers,bundle,collection,seo_*,sale_ends_at,published,demo,created_at` | `products`, `product_categories`, `product_media`, `variants` | **imported.** `published`→`status active/draft`; `demo`→`is_demo`; variants embedded → `variants` (`legacy_id`=legacy variant id; `option_values`, `options` derived); no variants → one default variant `<id>:default` (warning); `price`/`compare_price` already minor units; other fields (`specs`, `warranty`, `tiers`, `bundle`, `preorder`, `badge`, …) preserved in `products.attributes`; SKU clash → invalid. Not imported: `product_drafts` (56 in the sample), `revisions`, `version` |
| **inventory** `{id:'main', quantities{variant→int}, reservations{order→iso}}` | `stock_levels` + `stock_movements` in warehouse `MAIN` | **imported via `ledger.apply_movement(reason="count")`.** Legacy quantities are *net of open reservations*: `on_hand = qty + reserved_by_open_orders`, then `reason="reserve"` for the reserved part; order lines get `reserved_qty`/`warehouse_id`. Negative quantity clamped to 0 (warning); unknown variant → invalid. Applied once per variant (re-run skips) |
| **stock_movements** | – | not replayed (history); opening balance only |
| **coupons** `code,type,value,min_spend,max_uses,max_per_customer,enabled,starts_at,ends_at,product_ids,category_ids,used` | `discounts` | **imported** (needs `discounts` module). `percentage`→`percent` (1–100 else invalid), `fixed`→`amount_minor`; `used`→`used_count` (clamped to limit); scope ids remapped, unmapped ones dropped with warning; `max_per_customer=0` → unlimited |
| **orders** | `orders`, `order_lines`, `order_events`, `discount_redemptions` | **imported** (needs `commerce`). See §3 |
| order `customer{…}` / `user_id` | `customers` (guests deduped by normalized phone, else email) | **imported**; counters `orders_count/total_spent_minor/last_order_at` recomputed (non-cancelled) |
| **settings** `{id:'store', value{…}}` | `setting_values` | **imported where a canonical key exists** (§4); rest listed in `unmapped_settings` |
| **locations** | address text only | used to resolve district/division names for addresses; the new address data ships with the app |
| **provider_configs, integrations** | `secrets`/provider records | **manual, deliberately**: credentials are never migrated (legacy used a single key derived from `APP_SECRET`); pre-loaded ≠ verified (see payments/shipping) |
| **documents, themes, site_workspace, site_releases, font_blobs** | `content_*` (builder) | **manual**: legacy section/theme JSON is not convertible 1:1 to the builder document model; keep the export for reference |
| **media / *_blobs** | `data_dir/media` | **no importer** (binaries not exported by the script) |
| **reviews, product_questions, conversations, messages, attachments** | – | **no importer** |
| **gift_cards, affiliates(+ledger), loyalty_ledger, credit_ledger, return_requests, offer_pages, purchase_orders** | – | **no importer** (target modules not built or by decision) |
| **payment_transactions, webhook_events** | – | not migrated; refund history of an order is preserved as `order_events` (`kind=refund`) |
| **audit_logs** | – | kept in the export only (new chain is append-only/hash-linked; importing foreign history would break tamper evidence) |

## 3. Orders in detail

| Legacy | New | Rule |
|---|---|---|
| `id`, `number` (`VT-` + 8 hex) | `orders.number` (unique), `legacy_id_map(orders,<id>)` | number preserved; collision → invalid. New numbers come from `order_number_seq` |
| `idempotency_key`, `request_hash` | same | kept (`legacy:<id>` if absent) |
| order access link (HMAC with `APP_SECRET`) | `access_token_hash` | **not portable**: random hash, old emailed links stop working; track by number + phone |
| `status` | `status`, `fulfillment_status` | `pending, awaiting_payment, processing` as is; `shipped`/`out_for_delivery`→`shipped`; `delivered`; `cancelled`; `failed`→`cancelled` (+`cancel_reason`); `refunded`→`cancelled`+`payment_status=refunded`; `partially_refunded`→`delivered`+`partially_refunded`; `returned`; **`initiating`→dropped and reported**. *The canonical vocabulary belongs to the commerce owner: adjust `STATUS_MAP` if it differs* |
| `payment_status`, `payment_method` | same | passed through |
| `subtotal, shipping, tax, tax_inclusive, total, currency` | `*_minor`, `currency` | already poisha. `discount_minor` = discount + bundle + tier + points + credit + gift-card discounts; `gift_wrap_fee`→`payment_fee_minor` (closest additive fee column); full legacy breakdown kept in the `placed` event `data.legacy`. `total` is authoritative: a component mismatch is a warning |
| `refunded_amount`, `refunds[]` | `refunded_minor`, `order_events(kind=refund)` | `paid_minor` = total for paid/refunded statuses, raised to `refunded` if needed (CHECK `refunded <= paid`) |
| `customer{name,phone,email,address,city,postal_code,location}` | `phone` (normalized `01XXXXXXXXX`), `name`, `email`, `shipping_address` | missing phone → invalid; non-BD number → stored digits + warning |
| `items[]` | `order_lines` | variant/product linked via maps (missing → warning, line kept); `tier_saving`→`discount_minor` |
| `history[]`, `internal_notes[]` | `order_events` (`status`, `note`) | timestamps preserved |
| `coupon` | `discount_code`, `discount_redemptions` | redemption for non-cancelled orders when the discount exists |
| not carried | `points_*`, `credit_*`, `gift_code`, `affiliate_code`, `tracking*`, `bank_reference` | preserved only in the `placed` event `data.legacy` |

Reconciliation (always computed, also in dry-run): imported order count, `sum(total)`, `sum(refunded)` vs the source; stock available units vs source quantities for imported variants (valid immediately after import); product/variant/category counts.

## 4. Settings

| Legacy `settings.value` field | Canonical key | Status |
|---|---|---|
| `tagline`, `announcement`, `email`, `phone`, `address` | `store.tagline`, `store.announcement`, `store.support_email`, `store.support_phone`, `store.address` | imported unless the key is already customised |
| `mode` (`maintenance`) | `store.maintenance_mode` | imported (`mode=='maintenance'`) |
| `maintenance_message`, `tax_rate`, `tax_inclusive`, `return_window_days`, `seo_title`, `seo_description` | candidates `store.maintenance_message`, `tax.*`, `orders.return_window_days`, `seo.*` | applied **only when a canonical key with that name exists at import time**; otherwise reported unmapped (today: unmapped) |
| `brand`, `base_currency`, `timezone`, `country` | `Store` row | set by the install wizard |
| `shipping[]`, `payments[]`, `currencies[]`, `tax_zones[]` | shipping zones/rates, payment methods, currency & tax tables | manual (become real tables; credentials excluded) |
| `navigation`, `footer_links`, `header_config`, `footer_config`, `brand_*` | builder documents | manual |
| `features{}` | – | replaced by installed modules/plugins |
| `loyalty_*`, `affiliate_commission_pct`, `installments`, `gift_wrap_fee`, `mira_config`, `auth_config`, `search_synonyms`, `support_*`, `translations`, `workspace_path`, `demo_catalog` | – | unmapped (no canonical setting; `workspace_path`/`demo_catalog` are legacy defects) |

## 5. Routes (legacy module → new API area)

Counts from the AST inventory (346 route decorators; some belong to the installer app).

| Legacy module (routes) | New area | Module / state |
|---|---|---|
| `auth`, `auth_ext`, `roles`, `permissions` (15+15+4) | `/api/auth`, `/api/account`, `/api/staff` | identity — present |
| `catalog` (23), `admin` product/taxonomy | `/api/store/*` (public), `/api/catalog/*` | catalog — present |
| inventory endpoints (`admin`, `purchasing`) | `/api/inventory/*` | inventory — present |
| `commerce` (18), `offers`(8), `giftcards`(3) | `/api/store/cart|checkout|orders`, orders admin | commerce — models present, API in progress; offers/giftcards not ported |
| `payments` (5), `providers_admin`(9), `service_keys` | `/api/payments/*` | payments — in progress |
| `couriers` (6), `locations`(13) | `/api/shipping/*` | shipping — in progress |
| `cms`(13), `release_engine`(7), `themes`, `fonts`, `publishing_jobs` | `/api/content/*` | content — present |
| `ai`(8), `ai_tools`(15), `ai_monitor`, `mira_workspace` | `/api/ai/*` | ai — present |
| `support*`(28+13+4), `whatsapp`(6), `mailbox`(9), `email_admin`(11), `notifications` | `/api/messaging/*`, `/api/notifications` | messaging — present (partial) |
| `server` (10: health, sitemap, feeds), `diagnostics`, `monitor`, `alerts` | `/api/health`, `/api/system/*` | platform/ops — present |
| `installer` (3) | `/api/install/*` | platform — present |
| new, no legacy equivalent | `/api/system/backups*`, `/api/system/migration/runs` | ops/migration (this work) |
| `affiliates`(9), `accounts` loyalty/credit/returns | – | not yet ported |

## 6. Features

| Legacy feature | Rebuild |
|---|---|
| Demo catalog auto-seed | explicit, flagged `is_demo` |
| Installer wizard (separate app, owner setup key in env) | in-app, one-time bootstrap token |
| 39 theme presets, industry packs | content/builder presets (not imported from legacy JSON) |
| Mira/Mony/AI Studio | `ai` module |
| Backups via `backup-mongo.sh` | `ops` module: pg_dump + media, encryption, restore drill |
| Watchdog / uptime scripts | System console + external monitor (documented) |

## 7. Smoke test against a real legacy instance (2026-11-09)

Real MongoDB 7.0.14 + the unmodified legacy app (copy at `/tmp/legacy-baseline`), DB `voltora_legacy_smoke`, prefix `v1_`. Seed data booted by the legacy app; two guest orders placed through `POST /api/checkout` (1 Dhaka COD with marketing opt-in, 1 outside Dhaka with `+8801…` phone), owner set up through `/api/auth/setup`, one customer registered. Exported with `scripts/export-legacy-mongo.sh` (6,646 documents, 42 collections). Imported into a scratch PostgreSQL database created with `alembic upgrade head`:

| Run | Result |
|---|---|
| dry-run | 8 categories, 8 brands, 56 products, 112 variants, 2 users, 1 coupon, 2 orders, 2 guest customers, 112 stock levels, 46 settings fields read; **0 invalid**; reconciliation: orders 2/2, total 7,240,000 = 7,240,000 minor, refunded 0 = 0, stock 2,486 = 2,486 units, products 56/56, variants 112/112, categories 8/8 |
| apply | identical counts, all reconciliations OK |
| re-apply | 0 created; products 56 / orders 2 / inventory 112 reported `existing` |
| unmapped | 40 settings fields (see §4; 46 in the first apply, before module registries were loaded for the settings step — fixed), 18 collections reported `not_migrated` with reasons, 10 more listed as `unknown_collections` (`product_drafts`, `collections`, `offer_pages`, `shipments`, `webhook_events`, `affiliate_ledger`, `ai_*`, `attachments`; all empty or out of scope) |
| backup + restore drill on the migrated DB | encrypted backup 286,768 bytes; drill passed (sha256, decrypt, manifest, pg_restore, 77 tables, row counts, audit chain 27 events, secrets) in 0.4 s |

Not covered by the smoke test: a signed-in customer's order, refunds, coupons used in orders, real MFA secrets, non-argon2 hashes (these paths are covered by synthetic fixtures in `tests/test_legacy_migration.py`, which follow the legacy field shapes but are not real data). The two real orders were placed on a fresh database, so they contain no refunds, coupon redemptions or payment-provider state.
