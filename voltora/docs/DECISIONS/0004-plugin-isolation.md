# 0004 — Plugins never run inside the VOLTORA process

Status: accepted · Date: 2026-11-09 · Implementation: `backend/app/modules/plugins/manifest.py` (host API 1.x)

## Context

The requirements call for installable payment providers, couriers, themes, components, AI tools and business modules "without rewriting the core", versioned, compatible, auditable, isolated and testable. Arbitrary third-party Python in the merchant's process would give an extension the database, the vault key and every customer record.

## Decision

* An extension is a **signed-by-hash manifest package** (zip ≤ 5 MB, ≤ 200 entries, manifest ≤ 256 KB) with an id (`vendor.name`), semver, host-API range, declared **permissions** (a subset of `app/core/rbac.PERMISSIONS`), dependencies and extension points (UI slots such as `product.below_price`, components for the builder registry, settings, admin links, AI tools, webhooks).
* **Two execution models only**: *declarative* (components, widgets, settings, admin links, aliases of core read tools — data, interpreted by the host) and *remote* (the host calls the extension's HTTPS endpoint with HMAC-signed requests, restricted to `allowedHosts`, with timeouts, payload caps and quotas). No dynamic import of plugin code.
* Install is a **consent flow**: the manifest's permissions are shown, the grant is stored, installation starts `disabled`, a sandbox contract test (`sandbox_test`) must pass before a remote extension can be enabled; updates re-run it and re-ask consent when permissions grow; rollback restores the previous package version. Every transition is audited.
* Plugin secrets live in the vault under `owner_kind=plugin` (`SecretRecord`), are never returned to the browser, never put in events/audit/AI context.
* SSRF protection (`core/ssrf.py`) applies to every outbound extension call.

## Alternatives rejected

* In-process Python entry points (like WordPress): fastest to build, no isolation. Rejected.
* Subprocess/WASI sandbox for third-party Python: attractive later; adds an OS-specific runtime and a large attack/maintenance surface for v2.0. The manifest contract is kept runtime-agnostic so it can be added.
* Docker-per-plugin: Docker is not guaranteed on the VPS target (bare-metal path).

## Consequences

* Plugins cannot extend arbitrary server behaviour (e.g. a new payment adapter that needs custom Python must be a remote service speaking the provider contract). This is intentional; heavy first-party integrations ship as core modules.
* Quotas and health diagnostics are part of the platform, not optional.
* Marketplace trust (publisher identity, review) is process, not code, and is **not** solved by this ADR.
