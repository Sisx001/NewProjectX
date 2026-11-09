# 0003 — Transactional outbox for domain events and side effects

Status: accepted · Date: 2026-11-09

## Context

Legacy ran side effects (loyalty, affiliate, gift-card mint, notifications) inside one `try/except: pass` after `mark_paid` (L-9) and recorded webhook de-duplication *before* processing (L-3), so failures were lost silently and provider retries were dropped.

## Decision

* State changes and their events commit atomically: `emit(db, store_id, EV_X, payload, dedupe_key=None)` inserts an `outbox_events` row in the caller's transaction. Types are declared once with `event_type(name, description)` (the registry feeds automation triggers, webhooks and the AI capability graph).
* An in-process `OutboxWorker` claims `pending` rows, runs every `@on(event)` handler in its own transaction, and records per-handler `results`. Failures retry with back-off; after 6 attempts the event is `dead` and visible in **System → Events & jobs** where an operator with `system.manage` can retry it (audited).
* **Handlers must be idempotent**; `dedupe_key` (UNIQUE) makes producers idempotent (e.g. one `payment.captured` per provider event id) and is recorded in the same transaction as the handling, not before it.
* Periodic work uses `@periodic(name, every_seconds)` in the same worker thread; each job's last result/error is shown in the System console. The scheduled backup (`ops.scheduled_backup`) is such a job.
* Outbound webhooks and automation workflows subscribe to the same events; delivery attempts are their own rows with their own retry state (webhooks module).

## Consequences

* At-least-once delivery: duplicate handling is a design requirement, tested per handler.
* Ordering is per event, not global; consumers needing order read state, not event sequence.
* One worker thread per process: long handlers delay others. Heavy work (backups, drills) must run in background tasks/threads and report through records, as `ops` does (`BackupRecord.status=running`).
* Dead events must be monitored: the health endpoint reports outbox counts by status.
