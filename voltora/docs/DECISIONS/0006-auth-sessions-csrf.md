# 0006 — Server-side sessions, double-submit CSRF, argon2, TOTP

Status: accepted · Date: 2026-11-09 · Implementation: `app/core/{security,deps}.py`, `app/main.py` middleware, `app/modules/identity`

Verification note: cookie/CSRF/bootstrap-token/production-guard behaviour is read from the code above; per-IP + per-account throttling and the `mfa_verified` gating are **design requirements to be confirmed by identity-module tests**, not claims made by this document's author.

## Decision

* **Sessions**: opaque 256-bit tokens in an `HttpOnly`, `SameSite=Lax` cookie (`vt_session`, `Secure` whenever `public_url` is https or the environment is staging/production). Only `sha256(token)` is stored (`auth_sessions.token_hash`), with `expires_at`, `revoked_at`, IP/UA and `mfa_verified`. Staff and customers share the mechanism; the principal carries permissions resolved from memberships/roles on each request (no stale permission snapshot in the session — legacy copied `permissions[]` into the session).
* **CSRF**: unsafe methods under `/api/` with a session cookie require header `X-CSRF-Token` equal (constant-time) to the readable `vt_csrf` cookie; requests authenticated by `Authorization: Bearer` (API keys) are exempt; provider webhooks, `/api/install/*` and plugin webhook receivers are exempt because they carry their own signature/token. Cross-origin writes are additionally limited by `allowed_origins`.
* **Passwords**: argon2id (`argon2-cffi`), strength policy, timing equalisation for unknown users. Legacy argon2 hashes are accepted unchanged (verified by the importer test).
* **MFA**: TOTP (`pyotp`), secrets sealed with the vault, hashed one-time recovery codes; `security.require_staff_mfa` policy; privileged actions are gated on `mfa_verified`.
* **Lockout/rate limits**: failures are counted per account *and* per IP (fixes L-35, where per-email lockout let anyone lock the owner out), with progressive `locked_until`; `RateLimiter` guards login, reset, checkout and webhook-adjacent routes. Client IP is taken from `X-Forwarded-For` only when the TCP peer is in `VOLTORA_TRUSTED_PROXIES` (exact addresses, not CIDRs — deployment pins the proxy's address).
* **First run**: no default credentials. `/api/install/*` is protected by a one-time bootstrap token written to `<data_dir>/bootstrap-token` and invalidated on completion (replaces `OWNER_SETUP_KEY` in env, which stayed valid forever).
* **Production guards**: `Settings` refuses to start in staging/production with the dev secret, a secret < 32 chars, or no vault key.

## Consequences

* Server-side sessions need a DB read per request (indexed); revocation is immediate.
* Cookies make the API same-site by design; third-party embedding of the admin is unsupported (`X-Frame-Options: SAMEORIGIN`).
* `trusted_proxies` accepting exact IPs only forces the compose file to pin Caddy's address (`172.29.0.10`); CIDR support would be a small core change (open item for the platform owner).
