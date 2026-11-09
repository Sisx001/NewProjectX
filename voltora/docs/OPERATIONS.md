# Operations runbook

Audience: the person responsible for a VOLTORA installation. Commands assume bare metal (`/opt/voltora/current/backend`, env from `/etc/voltora/voltora.env`); for Docker prefix with `docker compose -f deploy/docker-compose.yml exec api` and drop the `sudo -u voltora` / env loading.

```bash
# bare-metal helper used below
vt() { ( set -a; . /etc/voltora/voltora.env; set +a; cd /opt/voltora/current/backend && sudo -E -u voltora .venv/bin/python -m "$@" ); }
```

## 1. Daily glance (2 minutes)

Admin → **System** (needs `system.read`): *Health* (database, worker status/last tick, outbox counts, scheduled jobs with last result/error, rate-limit blocks, payments mode, provider network switch), *Events & jobs* (dead events), *Logs* (filter by level/text/`request_id`), *Audit trail* (hash-chain verification), *Backups* (last success, last verified restore).

Healthy means: worker `running` with a recent tick; no `dead`/growing `pending` events; jobs `ok`; last backup < 3 days (the Backups tab warns); last restore drill within the quarter.

External monitoring (recommended, not built in): poll `https://<domain>/api/health` (200 + `{"status":"ok"}`) from a third-party uptime service and alert on 2 consecutive failures.

## 2. Backup and restore

**What a backup is**: one `.vbak` file in `<data_dir>/backups` = tar of `manifest.json` (sha256 of members, app version, alembic revision, row counts before/after), `database.dump` (`pg_dump -Fc`, taken from the *configured* database) and `media.tar` (when `<data_dir>/media` exists). With encryption the whole container is AES-256-GCM, chunked, key derived from the vault key (ADR 0007). The file's sha256 is stored in `backups`.

**Create**: UI System → Backups → *Back up now* (default encrypted); CLI `vt app.modules.ops.backup create --encrypt`; scheduled: Settings → Backups (`backup.enabled`, interval, encryption, retention count *and* days — a backup is deleted only when it is beyond the count **and** older than the days; the newest verified one is always kept; optional drill after each scheduled backup). Alternative: `systemctl enable --now voltora-backup.timer` (use one scheduler, not both).

**Verify (restore drill)**: UI *Verify by restoring* or `vt app.modules.ops.backup verify [id]`. It checks the file checksum, decrypts (authentication failure ⇒ tampered/wrong key), re-hashes members, restores into a disposable `voltora_drill_*` database, compares per-table row counts with the backup window, **re-verifies the audit hash chain of the restored data**, proves every sealed secret decrypts with the current vault key, then drops the scratch database and records `restore_verified_at` + the step report. A drill needs `CREATEDB` rights for the DB role and free disk ≈ database size. Run one after every upgrade and at least quarterly.

**Off-server copy (mandatory, not automated)**: backups on the same disk die with the server. Example nightly copy of encrypted files: `rclone copy /var/lib/voltora/backups remote:voltora-backups --include "*.vbak.enc"` or `rsync -a --include='*.vbak.enc' --exclude='*' /var/lib/voltora/backups/ backup-host:/srv/voltora/`. Download from the UI is audited (`backup.downloaded`) and requires `system.manage`; an *unencrypted* download contains customer data and password hashes.

**Restore for real** (disaster recovery or schema rollback). The CLI refuses to restore over the live database on purpose.
1. Provision (or keep) PostgreSQL; create an **empty** database: `createdb -h 127.0.0.1 -U voltora voltora_restored`.
2. Have the vault key that was active when the backup was made in the environment (`VOLTORA_VAULT_KEY`) — otherwise decryption fails by design.
3. `vt app.modules.ops.backup restore <backup-id> --to postgresql://voltora:PW@127.0.0.1:5432/voltora_restored --media-dir /var/lib/voltora/media`. If the `backups` table is gone (fresh server) run `python -c` free approach: copy the file into `<data_dir>/backups`, insert nothing — instead decrypt manually: `vt app.modules.ops.backup list` shows only registered files; for an unregistered file restore the *database* first from another backup, or ask for the manual procedure in `app/modules/ops/backup.py` (`decrypt_file`, then `tar -xf`, then `pg_restore --no-owner -d <db> database.dump`).
4. Stop the API; point `VOLTORA_DATABASE_URL` at `voltora_restored`; start; run `alembic upgrade head` (migrates forward if the backup was older); check System → Health and Audit trail → *Verify chain*.
5. Keep the old database until you are sure. Record the incident.

## 3. Upgrade and rollback

See `docs/DEPLOYMENT.md` §6. Pre-flight: read the release notes, confirm free disk ≥ 2× database size, confirm a recent verified backup exists, schedule outside peak hours (the API restarts, ~10–30 s). During: run the script (it backs up and drills first). After: `scripts/healthcheck.sh`, System → Health, place a sandbox test order, check Events & jobs for new dead events. Roll back code with the documented command; roll back data only by restoring the pre-upgrade backup (§2) because migrations are forward-only.

## 4. Incident triage with the System console

| Symptom | Look at | Likely cause / action |
|---|---|---|
| Site down / 502 | `systemctl status voltora-api`, `journalctl -u voltora-api -n 100`; Docker: `logs api`. `scripts/healthcheck.sh` | Config guard failed at start (secret/vault key missing), DB unreachable, migration error. Fix env, restart |
| Orders placed but no emails/webhooks | Health → worker `stopped`/stale tick; Events & jobs → `pending`/`dead` | Worker thread died (restart service). Dead events: read `last_error`, fix cause, **Retry** (audited) |
| A customer reports a failed payment | Logs filtered by their `request_id` (shown in error toasts and `X-Request-ID`); Events → `payment.*`; Audit trail by order | Provider outage vs signature/amount mismatch; never mark paid by hand without the provider record |
| Stock wrong | Inventory movements (ledger) for the variant; Audit | `stock_levels` is derived from movements: find the movement that diverges; adjust with a counted adjustment (reason, audited) |
| Slow admin | Health → DB size/connections, jobs | Long drills/backups running (Backups tab shows `running`), missing disk, connection pool exhaustion |
| Suspected tampering | Audit trail → **Verify chain**; failed login spikes (Logs `auth.*`, rate-limit blocks) | Chain break = a row was edited or deleted outside the app: restore from a verified backup into a new DB and compare; rotate secrets (§5) |
| Backup failed | Backups tab (error text), `system.manage` audit `backup.failed` | `pg_dump` missing/version older than server, disk full, DB permissions |
| Restore drill failed | Open the backup → step list shows which check failed | `sha256`/`decrypt` = file damaged or key mismatch; `secrets_decryptable` = vault key differs from the one used when secrets were stored; `audit_chain` = data integrity problem |

Always capture: time, `request_id`, affected order/customer ids, what changed recently (Audit trail + Settings revisions).

## 5. Secrets and key rotation

| Secret | Rotate when | Procedure | Impact |
|---|---|---|---|
| Admin passwords / sessions | staff leaves, suspected compromise | Team & roles → disable user, revoke sessions; user resets password | immediate |
| `VOLTORA_SECRET_KEY` | suspected leak, yearly | edit env, restart | all sessions and signed tokens invalid; users sign in again |
| Database password | staff with access leaves | `ALTER ROLE voltora PASSWORD '…'`, update `VOLTORA_DATABASE_URL`/`POSTGRES_PASSWORD`, restart | seconds of downtime |
| Provider credentials (payments, couriers, SMS, SMTP, AI) | provider advice, leak | Admin → the provider page → enter new value (stored sealed; old hint replaced; audited) | none beyond the provider |
| **`VOLTORA_VAULT_KEY`** | suspected leak, staff with server access leaves | procedure below | requires a short maintenance window |
| Backup encryption | derived from the vault key | rotates with it | **older encrypted backups need the old key** |

**Vault key rotation procedure** (offline re-seal; tested on synthetic data in `tests/test_ops_vault.py`, not yet rehearsed on a real installation):
1. Announce a maintenance window. Create and verify a backup (§2); copy it off-server. **Keep the old vault key** (password manager entry marked "old") until every backup made with it is past retention.
2. Generate the new key: `python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` and store it in the password manager first.
3. Stop the API (`systemctl stop voltora-api`) so no new secrets are written with the old key.
4. Dry run: `OLD_KEY=<old> NEW_KEY=<new> vt app.modules.ops.vault rotate --old-key-env OLD_KEY --new-key-env NEW_KEY` → expect `"ok": true` and counts per column (`secrets.sealed`, `users.mfa_secret_sealed`, `users.mfa_pending_sealed`, plus any column named `*_sealed` added by later modules). Any failure lists the rows that do not decrypt with the old key and **nothing is changed**.
5. Apply: same command with `--apply` (single transaction; it re-reads everything with the new key and reports `verified_with_new_key`).
6. Put the new key in `VOLTORA_VAULT_KEY` (`/etc/voltora/voltora.env` or `deploy/.env`), start the API, run `vt app.modules.ops.vault verify` (must say `"ok": true`).
7. Immediately create a new encrypted backup and run a drill (the drill's `secrets_decryptable` step proves the new key works end to end). Test one provider connection and one MFA login.
8. Rollback if step 6/7 fails: stop, restore the pre-rotation backup into a new DB with the **old** key (§2), or re-run `rotate` with the keys swapped.

**Not covered**: zero-downtime rotation (needs a key ring: `kid`-prefixed `sealed` values; deferred, ADR 0007), HSM/KMS, automatic rotation reminders.

## 6. Routine tasks

* Weekly: look at Events & jobs and Logs (level ≥ WARNING); apply OS security updates (`unattended-upgrades` is recommended and not configured by the installer).
* Monthly: restore drill from the *off-server* copy on a scratch machine; review staff and roles; review API keys/webhooks.
* Quarterly: vault/secret rotation review, upgrade to the latest release on staging first.
* Disk: `<data_dir>/media`, `<data_dir>/backups` and PostgreSQL grow; set retention deliberately and alert at 80 % disk.

## 7. Acceptance checklist for a new environment (what to run on a disposable VPS)

None of these has been executed by the authors of this runbook on real infrastructure.

1. Compose: `docker compose config`, `up -d --build`, all three services healthy, `https://<domain>` shows the wizard; bare metal: `install-ubuntu.sh --dry-run` then real run, `systemctl status voltora-api caddy`.
2. `curl -I` shows HSTS/CSP; `nmap` shows only 22/80/443.
3. Wizard completes; login with TOTP; token file no longer works.
4. Journeys: create product → stock → cart → COD checkout → order visible → cancel releases stock; sandbox online payment → refund; theme edit → publish → rollback; plugin install/disable; AI action proposal approved and audited; staff role denial.
5. Backup → verify; download; tamper with a copy → verify fails; restore into a new database and boot the app on it.
6. Upgrade to a newer commit with `upgrade-*.sh`; force a failure (break migration on a copy) and confirm automatic revert; vault key rotation rehearsal.
7. Reboot the server: services return without manual steps.
