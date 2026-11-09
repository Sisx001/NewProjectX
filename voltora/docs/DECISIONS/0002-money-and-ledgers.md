# 0002 — Money as integers; stock and payments as ledgers

Status: accepted · Date: 2026-11-09

## Context

Legacy money was already integer poisha (good) but refunds and stock were mutated through unconditional or stale-read updates: `refunded_amount` set vs `$inc` (L-5), refund sent to the provider before the local ledger (P-03), conditional-update result ignored (P-04), coupon/points never restored (L-16, L-17), stock held only as a map in one document (`inventory{id:'main'}`).

## Decision

1. **Money** is `BigInteger` minor units (`*_minor`) plus an ISO currency (`BDT`: 1 taka = 100 poisha). No floats anywhere in persistence or APIs; totals are computed server-side only; the client's price is an `expected_price` check, never an input.
2. **Stock** changes only through `inventory.ledger.apply_movement`: it row-locks `(variant, warehouse)` in a stable order (no deadlocks), enforces `on_hand >= 0`, `reserved >= 0`, `reserved <= on_hand` (unless backorders), and writes an append-only `stock_movements` row with the resulting balances. `stock_levels` is therefore always reproducible from movements. Reasons are explicit (`count`, `reserve`, `sale`, …); the legacy importer uses `count` for opening balances.
3. **Orders** carry denormalised totals (`subtotal/discount/shipping/fee/tax/total/paid/refunded`) with DB CHECKs (`total >= 0`, `refunded_minor <= paid_minor`) and a unique `(store_id, idempotency_key)`; order numbers come from a DB sequence with a UNIQUE constraint (fixes L-19).
4. **Refunds and payments** are rows written *before* any provider call (refund intent → provider call with a provider idempotency key → settle), inside transactions that lock the order; a refund can never exceed `paid - refunded` and cannot be applied twice. Provider callbacks are verified server-side and bound to the payment record (order, amount, currency).
5. **Discounts**: `used_count` is derived from `discount_redemptions` rows (`active|released`), so cancel/expiry releases the redemption (fixes L-16).
6. **Audit**: every money-moving mutation writes a hash-chained `audit_events` row in the same transaction.

## Consequences

* Concurrency correctness relies on PostgreSQL row locks and constraints, not on application discipline; tests must cover concurrent checkout and double refunds (owner: commerce/payments).
* Reports and the legacy-import reconciliation (`sum(total)`, `sum(refunded)`, stock units) are exact integer comparisons.
* Gift-wrap fee and multi-source discounts from legacy are collapsed into the new columns on import; the full legacy breakdown is retained in the order's `placed` event (`docs/MIGRATION_MAP.md` §3).
* Multi-currency requires per-currency exponent handling; only BDT is validated today.
