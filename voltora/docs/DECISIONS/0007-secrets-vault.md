# 0007 — Secrets vault and key hierarchy

Status: accepted · Date: 2026-11-09 · Implementation: `app/core/security.py` (`seal/unseal/mask`), `platform.models.SecretRecord`, `ops/backup.py`

## Context

Legacy used one Fernet key derived from `APP_SECRET` (SHA-256) for MFA secrets, provider keys, AI keys and order-link signing, with no rotation or versioning (L-37). Any leak of `APP_SECRET` exposed everything, and rotating it invalidated customer links and stored credentials at once.

## Decision

| Key | Purpose | Where | Loss means |
|---|---|---|---|
| `VOLTORA_SECRET_KEY` (≥ 32 chars) | session/HMAC signing, order-access tokens | environment | all sessions invalid (users sign in again) — recoverable |
| `VOLTORA_VAULT_KEY` (Fernet) | seals stored credentials (`SecretRecord.sealed`), MFA secrets | environment, **not in the database or backups** | sealed secrets unrecoverable (re-enter credentials); encrypted backups unreadable |
| backup key | AES-256-GCM key for encrypted backups, `HKDF-SHA256(vault_key, random 16-byte salt, "voltora-backup-v1")` per file | derived on demand | see above |

* Secrets are stored as `SecretRecord(store_id, ref, sealed, hint, owner_kind)`; APIs return only `mask()` hints (`••••1234`). Plaintext exists only inside the server process while calling the provider. Secrets never enter audit data (`redact()` by key name), outbox payloads, logs, backups' manifests or AI context; AI tools receive references and results, not credentials.
* In development without a vault key a key is derived from `secret_key`; configuration validation rejects that in staging/production.
* **Rotation** is operator-driven and offline: see `docs/OPERATIONS.md` §5. The current implementation seals with a single key and does **not** support a key ring; a re-seal tool is a documented gap, not a hidden feature.
* Restore drills decrypt every `secrets` row with the *current* vault key (`secrets_decryptable` step), so a backup that cannot be unsealed is detected before it is needed.

## Alternatives

* KMS/HSM: no cloud dependency assumed for a VPS product; the `seal/unseal` seam allows one later.
* Per-secret data keys with a key ring (`kid` prefix in `sealed`): the right next step for zero-downtime rotation; deferred (needs a migration of `sealed` format).

## Consequences

* The vault key must be backed up separately from database backups (a password manager entry); documented in `.env.example`, `DEPLOYMENT.md` and the Backups tab warning.
* Because backup encryption derives from the vault key, **rotating the vault key makes older encrypted backups unreadable with the new key** unless the old key is retained; the runbook requires re-backing-up after rotation and keeping the old key until the retention window passes.
