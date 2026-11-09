"""Extension lifecycle: catalog, packages, consent, install/update/rollback/enable/disable/uninstall, sandbox
contract test, health, settings/secrets, and the runtime contributions (AI tools, components, event endpoints)."""
from __future__ import annotations

import json
import logging
import re
import secrets as pysecrets
from functools import lru_cache
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import HOST_API_VERSION
from ...core.errors import AppError, Conflict
from ...core.rbac import PERMISSIONS, has_permission
from ...core.services import Actor, audit, emit, event_type, limiter
from ...db import utcnow
from ..ai.schema import SchemaError, apply_defaults, validate
from ..webhooks.outbound import SIGNATURE_HEADER, request, scrub, sign, verify_signature
from .manifest import Manifest, compatible, remote_urls, satisfies, validate_manifest
from .models import PluginInstallation, PluginPackage

log = logging.getLogger("voltora.plugins")
CATALOG_DIR = Path(__file__).parent / "catalog"
SIGNING = "__signing"
REMOTE_TIMEOUT = 10.0
REMOTE_CALLS_PER_MINUTE = 120

EV_INSTALLED = event_type("plugin.installed", "An extension was installed")
EV_ENABLED = event_type("plugin.enabled", "An extension was enabled")
EV_DISABLED = event_type("plugin.disabled", "An extension was disabled")
EV_UNINSTALLED = event_type("plugin.uninstalled", "An extension was uninstalled")


def secret_ref(plugin_id: str, key: str) -> str:
    return f"plugin.{plugin_id}.{key}"


# ---------------------------------------------------------------- catalog
@lru_cache
def catalog() -> dict[str, dict]:
    out = {}
    for f in sorted(CATALOG_DIR.glob("*.json")):
        raw = json.loads(f.read_text(encoding="utf-8"))
        try:
            m = validate_manifest(raw)
        except AppError as exc:
            log.error("Catalog manifest %s is invalid: %s", f.name, exc.message)
            continue
        out[m.id] = raw
    return out


def manifest_of(inst: PluginInstallation) -> Manifest:
    return Manifest.model_validate(inst.manifest)


def get_installation(db: Session, store_id, plugin_id: str, *, lock: bool = False) -> PluginInstallation | None:
    q = select(PluginInstallation).where(PluginInstallation.store_id == store_id,
                                         PluginInstallation.plugin_id == plugin_id)
    if lock:
        q = q.with_for_update()
    return db.execute(q).scalar_one_or_none()


# ---------------------------------------------------------------- preview / consent
def dependency_report(db: Session, store_id, m: Manifest) -> list[dict]:
    out = []
    for dep, rng in m.dependencies.items():
        inst = get_installation(db, store_id, dep)
        ok = inst is not None and inst.status == "enabled" and satisfies(inst.version, rng)
        out.append({"id": dep, "range": rng, "installed": inst.version if inst and inst.status != "uninstalled"
                    else None, "enabled": bool(inst and inst.status == "enabled"), "ok": ok})
    return out


def preview(db: Session, store_id, pkg: PluginPackage, installer_perms: frozenset) -> dict:
    m = validate_manifest(pkg.manifest)
    inst = get_installation(db, store_id, m.id)
    deps = dependency_report(db, store_id, m)
    over = [p for p in m.permissions if not has_permission(installer_perms, p)]
    warnings = []
    if remote_urls(m):
        warnings.append("This extension calls services operated by its publisher; VOLTORA will send signed requests "
                        f"only to: {', '.join(m.allowedHosts)}.")
    if m.extensions.payment_providers or m.extensions.couriers:
        warnings.append("Payment/courier adapters stay disabled until a contract test passes and an owner verifies "
                        "them with sandbox credentials.")
    if pkg.source == "upload":
        warnings.append("Uploaded package: not reviewed by VOLTORA. Check the publisher before installing.")
    return {
        "package_id": str(pkg.id), "source": pkg.source, "sha256": pkg.sha256, "manifest": pkg.manifest,
        "readme": pkg.readme,
        "compatibility": {"host_api": HOST_API_VERSION, "requires": m.hostApi, "ok": compatible(m)},
        "dependencies": deps,
        "permissions": [{"key": p, "description": PERMISSIONS.get(p, ""), "you_hold": has_permission(
            installer_perms, p)} for p in m.permissions],
        "allowed_hosts": m.allowedHosts,
        "secrets": [s.model_dump() for s in m.secrets],
        "extension_points": {k: len(v) for k, v in m.extensions.model_dump().items() if v},
        "warnings": warnings,
        "current": {"version": inst.version, "status": inst.status} if inst else None,
        "can_install": compatible(m) and all(d["ok"] for d in deps) and not over,
        "blocked_reasons": ([] if compatible(m) else [f"Requires host API {m.hostApi}; this VOLTORA provides "
                                                      f"{HOST_API_VERSION}"])
        + [f"Needs {d['id']} {d['range']} installed and enabled" for d in deps if not d["ok"]]
        + ([f"You cannot grant permissions you do not hold: {', '.join(over)}"] if over else []),
    }


def create_package(db: Session, store_id, raw: dict, source: str, sha256: str, actor: Actor,
                   filename: str | None = None, readme: str = "") -> PluginPackage:
    m = validate_manifest(raw)
    pkg = PluginPackage(store_id=store_id, plugin_id=m.id, version=m.version, manifest=raw, readme=readme or m.docs,
                        source=source, filename=filename, sha256=sha256, created_by=actor.id)
    db.add(pkg)
    db.flush()
    return pkg


def _check_install(db: Session, store_id, m: Manifest, granted: list[str], installer_perms: frozenset) -> None:
    if not compatible(m):
        raise Conflict("incompatible", f"{m.name} requires host API {m.hostApi}; this VOLTORA provides "
                       f"{HOST_API_VERSION}")
    missing = [d for d in dependency_report(db, store_id, m) if not d["ok"]]
    if missing:
        raise Conflict("dependency_missing", "Install and enable dependencies first: " +
                       ", ".join(f"{d['id']} {d['range']}" for d in missing), details=missing)
    if sorted(set(granted)) != sorted(set(m.permissions)):
        raise AppError("consent_required", "Review and accept exactly the permissions this extension requests",
                       details={"requested": m.permissions})
    over = [p for p in m.permissions if not has_permission(installer_perms, p)]
    if over:
        raise AppError("forbidden", f"You cannot grant permissions you do not hold: {', '.join(over)}", status=403)


def _new_signing_secret(db: Session, store_id, plugin_id: str) -> str:
    from ..webhooks.service import new_secret

    return new_secret(db, store_id, secret_ref(plugin_id, SIGNING), owner_kind="plugin", owner_ref=plugin_id,
                      value="psk_" + pysecrets.token_urlsafe(32))


def install(db: Session, store_id, pkg: PluginPackage, granted: list[str], actor: Actor,
            installer_perms: frozenset) -> tuple[PluginInstallation, str]:
    m = validate_manifest(pkg.manifest)
    _check_install(db, store_id, m, granted, installer_perms)
    inst = get_installation(db, store_id, m.id, lock=True)
    if inst is not None and inst.status != "uninstalled":
        raise Conflict("already_installed", f"{m.name} is already installed; use update instead")
    kept = inst is not None
    if inst is None:
        inst = PluginInstallation(store_id=store_id, plugin_id=m.id, settings={})
        db.add(inst)
    inst.name, inst.version, inst.manifest, inst.source = m.name, m.version, pkg.manifest, pkg.source
    inst.package_sha256, inst.granted_permissions = pkg.sha256, sorted(m.permissions)
    inst.settings = apply_defaults(m.settings, inst.settings or {})
    inst.status, inst.installed_by, inst.health, inst.sandbox = "disabled", actor.id, {}, {}
    db.flush()
    raw = _new_signing_secret(db, store_id, m.id)
    inst.sandbox = sandbox_test(db, inst)
    _call_hook(db, inst, "install")
    audit(db, store_id, actor, "plugins.installed", "plugin", m.id,
          {"version": m.version, "source": pkg.source, "sha256": pkg.sha256, "permissions": m.permissions,
           "data_restored": kept})
    emit(db, store_id, EV_INSTALLED, {"plugin_id": m.id, "version": m.version})
    return inst, raw


def update(db: Session, store_id, inst: PluginInstallation, pkg: PluginPackage, granted: list[str], actor: Actor,
           installer_perms: frozenset) -> PluginInstallation:
    m = validate_manifest(pkg.manifest)
    if m.id != inst.plugin_id:
        raise AppError("wrong_package", "This package is for a different extension")
    _check_install(db, store_id, m, granted, installer_perms)
    was_enabled = inst.status == "enabled"
    inst.history = [*(inst.history or []), {"version": inst.version, "manifest": inst.manifest,
                                            "settings": inst.settings, "source": inst.source,
                                            "package_sha256": inst.package_sha256,
                                            "granted_permissions": inst.granted_permissions,
                                            "at": utcnow().isoformat()}][-5:]
    _apply_version(db, inst, m, pkg.manifest, pkg.source, pkg.sha256, actor)
    if was_enabled:
        _set_enabled(db, inst, actor, enabled=inst.sandbox.get("status") == "passed" or not _needs_remote_check(m),
                     reason="update")
    audit(db, store_id, actor, "plugins.updated", "plugin", m.id, {"from": inst.history[-1]["version"],
                                                                   "to": m.version})
    return inst


def _apply_version(db: Session, inst: PluginInstallation, m: Manifest, raw: dict, source: str, sha: str,
                   actor: Actor, settings: dict | None = None) -> None:
    inst.version, inst.manifest, inst.name, inst.source, inst.package_sha256 = m.version, raw, m.name, source, sha
    inst.granted_permissions = sorted(m.permissions)
    base = settings if settings is not None else inst.settings
    known = set((m.settings.get("properties") or {}).keys())
    inst.settings = apply_defaults(m.settings, {k: v for k, v in (base or {}).items() if k in known})
    inst.sandbox = sandbox_test(db, inst)
    sync_endpoints(db, inst)


def rollback(db: Session, store_id, inst: PluginInstallation, actor: Actor) -> PluginInstallation:
    if not inst.history:
        raise Conflict("no_previous_version", "There is no previous version to roll back to")
    prev = inst.history[-1]
    m = validate_manifest(prev["manifest"])
    if not compatible(m):
        raise Conflict("incompatible", f"Version {m.version} is not compatible with this VOLTORA")
    current = inst.version
    inst.history = inst.history[:-1]
    _apply_version(db, inst, m, prev["manifest"], prev["source"], prev["package_sha256"], actor,
                   settings=prev.get("settings"))
    if inst.status == "enabled" and _needs_remote_check(m) and inst.sandbox.get("status") != "passed":
        _set_enabled(db, inst, actor, enabled=False, reason="rollback: contract test not passing")
    audit(db, store_id, actor, "plugins.rolled_back", "plugin", inst.plugin_id, {"from": current, "to": m.version})
    return inst


def _dependents(db: Session, store_id, plugin_id: str) -> list[str]:
    rows = db.execute(select(PluginInstallation).where(PluginInstallation.store_id == store_id,
                                                       PluginInstallation.status == "enabled")).scalars().all()
    return [r.plugin_id for r in rows if plugin_id in (r.manifest.get("dependencies") or {})]


def uninstall(db: Session, store_id, inst: PluginInstallation, actor: Actor, purge: bool) -> None:
    from ..platform.models import SecretRecord
    from ..webhooks.models import WebhookEndpoint

    deps = _dependents(db, store_id, inst.plugin_id)
    if deps:
        raise Conflict("has_dependents", f"Disable these extensions first: {', '.join(deps)}", details=deps)
    _call_hook(db, inst, "uninstall")
    for ep in db.execute(select(WebhookEndpoint).where(WebhookEndpoint.store_id == store_id,
                                                       WebhookEndpoint.plugin_id == inst.plugin_id)).scalars():
        db.delete(ep)
    if purge:
        for s in db.execute(select(SecretRecord).where(SecretRecord.store_id == store_id,
                                                       SecretRecord.owner_kind == "plugin",
                                                       SecretRecord.owner_ref == inst.plugin_id)).scalars():
            db.delete(s)
        db.delete(inst)
    else:
        inst.status, inst.enabled_at = "uninstalled", None
    audit(db, store_id, actor, "plugins.uninstalled", "plugin", inst.plugin_id,
          {"version": inst.version, "data": "purged" if purge else "kept"})
    emit(db, store_id, EV_UNINSTALLED, {"plugin_id": inst.plugin_id, "purged": purge})


# ---------------------------------------------------------------- enable / disable
def _needs_remote_check(m: Manifest) -> bool:
    return bool(remote_urls(m))


def readiness(db: Session, inst: PluginInstallation) -> list[str]:
    m = manifest_of(inst)
    problems = []
    try:
        validate(m.settings, inst.settings or {})
    except SchemaError as exc:
        problems.append(f"Settings: {exc}")
    have = configured_secrets(db, inst)
    problems += [f"Secret “{s.label}” is required" for s in m.secrets if s.required and s.key not in have]
    for sub in m.extensions.event_subscriptions:
        if _subscription_active(sub, inst.settings):
            if sub.url_secret and sub.url_secret not in have:
                problems.append(f"{sub.key}: secret {sub.url_secret} is not set")
            if sub.telegram and (sub.telegram.token_secret not in have or
                                 not inst.settings.get(sub.telegram.chat_id_setting)):
                problems.append(f"{sub.key}: Telegram bot token and chat id are required")
            if sub.url_setting and not inst.settings.get(sub.url_setting):
                problems.append(f"{sub.key}: setting {sub.url_setting} is empty")
    deps = [d for d in dependency_report(db, inst.store_id, m) if not d["ok"]]
    problems += [f"Dependency {d['id']} {d['range']} is not enabled" for d in deps]
    if _needs_remote_check(m) and inst.sandbox.get("status") != "passed":
        problems.append("The sandbox contract test has not passed for this version")
    return problems


def _set_enabled(db: Session, inst: PluginInstallation, actor: Actor, enabled: bool, reason: str = "") -> None:
    inst.status = "enabled" if enabled else "disabled"
    inst.enabled_at = utcnow() if enabled else None
    sync_endpoints(db, inst)
    _call_hook(db, inst, "enable" if enabled else "disable")
    emit(db, inst.store_id, EV_ENABLED if enabled else EV_DISABLED, {"plugin_id": inst.plugin_id,
                                                                      "version": inst.version, "reason": reason})


def enable(db: Session, inst: PluginInstallation, actor: Actor) -> PluginInstallation:
    if inst.status == "uninstalled":
        raise Conflict("not_installed", "Reinstall the extension first")
    problems = readiness(db, inst)
    if problems:
        raise Conflict("not_ready", "This extension cannot be enabled yet: " + "; ".join(problems),
                       details=problems)
    _set_enabled(db, inst, actor, True)
    audit(db, inst.store_id, actor, "plugins.enabled", "plugin", inst.plugin_id, {"version": inst.version})
    return inst


def disable(db: Session, inst: PluginInstallation, actor: Actor) -> PluginInstallation:
    deps = _dependents(db, inst.store_id, inst.plugin_id)
    if deps:
        raise Conflict("has_dependents", f"Disable these extensions first: {', '.join(deps)}", details=deps)
    _set_enabled(db, inst, actor, False)
    audit(db, inst.store_id, actor, "plugins.disabled", "plugin", inst.plugin_id, {"version": inst.version})
    return inst


# ---------------------------------------------------------------- settings & secrets
def configured_secrets(db: Session, inst: PluginInstallation) -> dict[str, str | None]:
    from ..platform.models import SecretRecord

    rows = db.execute(select(SecretRecord.ref, SecretRecord.hint).where(
        SecretRecord.store_id == inst.store_id, SecretRecord.owner_kind == "plugin",
        SecretRecord.owner_ref == inst.plugin_id)).all()
    prefix = secret_ref(inst.plugin_id, "")
    return {ref[len(prefix):]: hint for ref, hint in rows if ref.startswith(prefix) and not ref.endswith(SIGNING)}


def update_settings(db: Session, inst: PluginInstallation, values: dict, actor: Actor) -> dict:
    m = manifest_of(inst)
    merged = apply_defaults(m.settings, values)
    try:
        validate(m.settings, merged)
    except SchemaError as exc:
        raise AppError("invalid_settings", str(exc), status=422, details=exc.errors) from exc
    inst.settings = merged
    sync_endpoints(db, inst)
    audit(db, inst.store_id, actor, "plugins.settings_updated", "plugin", inst.plugin_id, {"keys": sorted(merged)})
    return merged


def update_secrets(db: Session, inst: PluginInstallation, values: dict[str, str | None], actor: Actor) -> None:
    from ..platform.models import SecretRecord
    from ..webhooks.service import new_secret

    m = manifest_of(inst)
    known = {s.key for s in m.secrets}
    for k, v in values.items():
        if k not in known:
            raise AppError("unknown_secret", f"{m.name} does not declare a secret named {k}")
        if v:
            new_secret(db, inst.store_id, secret_ref(inst.plugin_id, k), owner_kind="plugin",
                       owner_ref=inst.plugin_id, value=v.strip())
        else:
            db.query(SecretRecord).filter(SecretRecord.store_id == inst.store_id,
                                          SecretRecord.ref == secret_ref(inst.plugin_id, k)).delete()
    audit(db, inst.store_id, actor, "plugins.secrets_updated", "plugin", inst.plugin_id, {"keys": sorted(values)})


def _secret(db: Session, inst: PluginInstallation, key: str) -> str | None:
    from ..webhooks.service import read_secret

    return read_secret(db, inst.store_id, secret_ref(inst.plugin_id, key))


# ---------------------------------------------------------------- remote calls
def remote_call(db: Session, inst: PluginInstallation, url: str, payload: dict) -> tuple[int, object | None, dict]:
    """Signed POST to an extension endpoint (allowedHosts only, timeout, quota). Returns status, json, headers."""
    m = manifest_of(inst)
    limiter.hit(f"plugin:{inst.store_id}:{inst.plugin_id}", REMOTE_CALLS_PER_MINUTE, 60)
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    secret = _secret(db, inst, SIGNING) or ""
    sig, ts = sign(secret, body)
    headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sig, "X-Voltora-Plugin": inst.plugin_id,
               "X-Voltora-Timestamp": str(ts), "User-Agent": "VOLTORA-Extensions/1"}
    try:
        r = request("POST", url, content=body, headers=headers, timeout=REMOTE_TIMEOUT, allowed_hosts=m.allowedHosts,
                    max_bytes=256_000)
    except httpx.HTTPError as exc:
        raise AppError("plugin_unreachable", scrub(f"{type(exc).__name__}: {exc}", secret)[:300], status=502) from exc
    return r.status, r.json, {"text": r.text}


def _call_hook(db: Session, inst: PluginInstallation, name: str) -> None:
    url = (inst.manifest.get("hooks") or {}).get(name)
    if not url:
        return
    try:
        status, _data, _ = remote_call(db, inst, url, {"type": f"voltora.lifecycle.{name}", "plugin": inst.plugin_id,
                                                       "version": inst.version})
        result = {"status": "ok" if 200 <= status < 300 else "failed", "http": status}
    except AppError as exc:
        result = {"status": "skipped" if exc.code == "network_disabled" else "failed", "error": exc.message}
    inst.health = {**(inst.health or {}), f"hook_{name}": {**result, "at": utcnow().isoformat()}}


def sandbox_test(db: Session, inst: PluginInstallation) -> dict:
    """Contract test: manifest, compatibility, dependencies, settings, hosts, signing round-trip and a signed
    challenge ping to every publisher-operated endpoint (only when outbound network is enabled)."""
    steps: list[dict] = []

    def step(name, status, detail=""):
        steps.append({"name": name, "status": status, "detail": detail})

    try:
        m = validate_manifest(inst.manifest)
        step("manifest", "passed", f"{m.id}@{m.version}")
    except AppError as exc:
        step("manifest", "failed", exc.message)
        return {"status": "failed", "steps": steps, "at": utcnow().isoformat()}
    step("compatibility", "passed" if compatible(m) else "failed", f"host {HOST_API_VERSION} vs {m.hostApi}")
    deps = dependency_report(db, inst.store_id, m)
    step("dependencies", "passed" if all(d["ok"] for d in deps) else "failed",
         ", ".join(f"{d['id']}:{'ok' if d['ok'] else 'missing'}" for d in deps) or "none")
    try:
        validate(m.settings, inst.settings or {})
        step("settings", "passed")
    except SchemaError as exc:
        step("settings", "failed", str(exc))
    secret = _secret(db, inst, SIGNING) or ""
    sample = b'{"type":"voltora.selftest"}'
    sig, _ = sign(secret, sample)
    step("signing", "passed" if secret and verify_signature(secret, sig, sample) else "failed",
         "HMAC-SHA256 over timestamp.body")
    for purpose, url in remote_urls(m):
        try:
            from ..webhooks.outbound import validate_url

            validate_url(url, allowed_hosts=m.allowedHosts)
        except AppError as exc:
            step(f"host:{purpose}", "failed", exc.message)
            continue
        challenge = pysecrets.token_hex(16)
        try:
            status, data, raw = remote_call(db, inst, url, {"type": "voltora.ping", "challenge": challenge,
                                                            "purpose": purpose})
        except AppError as exc:
            step(f"ping:{purpose}", "skipped" if exc.code == "network_disabled" else "failed", exc.message)
            continue
        ok = 200 <= status < 300 and isinstance(data, dict) and data.get("challenge") == challenge
        step(f"ping:{purpose}", "passed" if ok else "failed",
             f"HTTP {status}" + ("" if ok else " (expected JSON echo of the challenge)"))
    for c in m.extensions.builder_components:
        step(f"component:{c.get('type')}", "passed", "declarative; validated against the builder registry")
    statuses = {s["status"] for s in steps}
    overall = "failed" if "failed" in statuses else "incomplete" if "skipped" in statuses else "passed"
    return {"status": overall, "steps": steps, "at": utcnow().isoformat()}


def health_check(db: Session, inst: PluginInstallation) -> dict:
    from ..webhooks.models import WebhookEndpoint

    problems = readiness(db, inst) if inst.status != "enabled" else []
    checks: list[dict] = []
    url = (inst.manifest.get("hooks") or {}).get("health")
    if url:
        try:
            status, data, _ = remote_call(db, inst, url, {"type": "voltora.health"})
            checks.append({"name": "remote", "ok": 200 <= status < 300, "detail": f"HTTP {status}"})
        except AppError as exc:
            checks.append({"name": "remote", "ok": False, "detail": exc.message})
    for ep in db.execute(select(WebhookEndpoint).where(WebhookEndpoint.store_id == inst.store_id,
                                                       WebhookEndpoint.plugin_id == inst.plugin_id)).scalars():
        checks.append({"name": f"delivery:{ep.name}", "ok": ep.consecutive_failures < 3,
                       "detail": f"last {ep.last_status or 'never delivered'}, "
                                 f"{ep.consecutive_failures} consecutive failures"})
    try:
        validate(manifest_of(inst).settings, inst.settings or {})
        checks.append({"name": "configuration", "ok": True, "detail": "settings valid"})
    except SchemaError as exc:
        checks.append({"name": "configuration", "ok": False, "detail": str(exc)})
    ok = all(c["ok"] for c in checks) and not problems
    status = "healthy" if ok and inst.status == "enabled" else ("needs_configuration" if problems else
                                                                 "degraded" if not ok else inst.status)
    inst.health = {**(inst.health or {}), "status": status, "checks": checks, "problems": problems,
                   "at": utcnow().isoformat()}
    if inst.status == "enabled" and not ok:
        inst.status = "broken"
    elif inst.status == "broken" and ok:
        inst.status = "enabled"
    return inst.health


# ---------------------------------------------------------------- event subscriptions → webhook endpoints
def _subscription_active(sub, settings: dict) -> bool:
    return all(settings.get(k) == v for k, v in (sub.when_setting or {}).items())


def sync_endpoints(db: Session, inst: PluginInstallation) -> None:
    from ..webhooks.models import WebhookEndpoint

    m = manifest_of(inst)
    existing = {ep.name: ep for ep in db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.store_id == inst.store_id, WebhookEndpoint.plugin_id == inst.plugin_id)).scalars()}
    wanted = set()
    for sub in m.extensions.event_subscriptions:
        name = f"{inst.plugin_id}:{sub.key}"
        wanted.add(name)
        events = (inst.settings.get(sub.events_setting) if sub.events_setting else None) or sub.events
        ep = existing.get(name)
        if ep is None:
            ep = WebhookEndpoint(store_id=inst.store_id, name=name, owner_kind="plugin", plugin_id=inst.plugin_id,
                                 secret_ref=secret_ref(inst.plugin_id, SIGNING))
            db.add(ep)
        ep.url = sub.url
        ep.events, ep.format = list(events), sub.format
        ep.target = sub.model_dump()
        ep.allowed_hosts = m.allowedHosts
        ep.description = f"Managed by extension {m.name}"
        ep.enabled = inst.status == "enabled" and _subscription_active(sub, inst.settings or {})
    for name, ep in existing.items():
        if name not in wanted:
            db.delete(ep)


def resolve_endpoint_target(db: Session, ep) -> tuple[str | None, str | None, list[str]]:
    """Destination for an extension-managed endpoint, resolved from the extension's settings/secrets."""
    inst = get_installation(db, ep.store_id, ep.plugin_id)
    if inst is None or inst.status not in ("enabled", "broken"):
        raise AppError("plugin_disabled", f"Extension {ep.plugin_id} is not enabled")
    t = ep.target or {}
    if t.get("telegram"):
        token = _secret(db, inst, t["telegram"]["token_secret"])
        chat = inst.settings.get(t["telegram"]["chat_id_setting"])
        if not token or not chat:
            raise AppError("webhook_unconfigured", "Telegram bot token or chat id is missing")
        return f"https://api.telegram.org/bot{token}/sendMessage", str(chat), [token]
    if t.get("url_secret"):
        url = _secret(db, inst, t["url_secret"])
        return url, None, [url] if url else []
    if t.get("url_setting"):
        return inst.settings.get(t["url_setting"]), None, []
    return ep.url, None, []


# ---------------------------------------------------------------- runtime contributions
def _enabled(db: Session, store_id) -> list[PluginInstallation]:
    return list(db.execute(select(PluginInstallation).where(PluginInstallation.store_id == store_id,
                                                            PluginInstallation.status == "enabled")
                           .order_by(PluginInstallation.plugin_id)).scalars())


def tool_name(plugin_id: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", plugin_id.lower())
    return f"x_{slug}_{name}"[:64]


def plugin_tools(db: Session, store_id) -> dict:
    from ..ai.tools import TOOLS, Tool

    out = {}
    for inst in _enabled(db, store_id):
        m = manifest_of(inst)
        for spec in m.extensions.ai_tools:
            if spec.permission not in inst.granted_permissions:
                continue
            name = tool_name(m.id, spec.name)
            out[name] = Tool(name=name, description=f"[{m.name}] {spec.description}",
                             input_schema=spec.input_schema, permission=spec.permission,
                             side_effect=spec.side_effect, fn=_tool_fn(inst.plugin_id, spec, TOOLS),
                             source=f"plugin:{m.id}")
    return out


def _tool_fn(plugin_id: str, spec, core_tools: dict):
    def fn(ctx, args: dict) -> dict:
        inst = get_installation(ctx.db, ctx.store_id, plugin_id)
        if inst is None or inst.status != "enabled":
            from ..ai.tools import ToolUnavailable

            raise ToolUnavailable(f"Extension {plugin_id} is not enabled")
        if spec.handler.type == "core_alias":
            merged = {**args, **spec.handler.fixed_args}
            for arg, key in spec.handler.args_from_settings.items():
                if inst.settings.get(key) is not None:
                    merged[arg] = inst.settings[key]
            core = core_tools[spec.handler.tool]
            return core.fn(ctx, validate(core.input_schema, merged))
        status, data, _ = remote_call(ctx.db, inst, spec.handler.url, {"type": "voltora.ai_tool",
                                                                       "tool": spec.name, "arguments": args})
        if not 200 <= status < 300:
            raise ValueError(f"Extension endpoint returned HTTP {status}")
        return {"extension": plugin_id, "data": data}

    return fn


def public_settings(m: Manifest, settings: dict) -> dict:
    props = m.settings.get("properties") or {}
    return {k: settings.get(k) for k, v in props.items() if v.get("x-public")}


def storefront_components(db: Session, store_id) -> dict:
    components, widgets = [], []
    for inst in _enabled(db, store_id):
        m = manifest_of(inst)
        for c in m.extensions.builder_components:
            components.append({**c, "source": f"plugin:{m.id}", "plugin": {"id": m.id, "version": m.version}})
        for w in m.extensions.storefront_widgets:
            props = {**w.props, **{p: inst.settings.get(s) for p, s in w.props_from_settings.items()}}
            widgets.append({"plugin_id": m.id, "key": w.key, "label": w.label, "slot": w.slot,
                            "component": w.component, "props": props})
    return {"components": components, "widgets": widgets}


def admin_links(db: Session, store_id) -> list[dict]:
    return [{"plugin_id": i.plugin_id, "plugin": i.name, **lk}
            for i in _enabled(db, store_id) for lk in (i.manifest.get("extensions") or {}).get("admin_links", [])]


def installed_summary(db: Session, store_id) -> list[dict]:
    """Capability-graph view: what each installed extension contributes and whether it is active."""
    rows = db.execute(select(PluginInstallation).where(PluginInstallation.store_id == store_id,
                                                       PluginInstallation.status != "uninstalled")).scalars().all()
    out = []
    for i in rows:
        ext = i.manifest.get("extensions") or {}
        out.append({"id": i.plugin_id, "name": i.name, "version": i.version, "status": i.status,
                    "permissions": i.granted_permissions,
                    "contributes": {k: [x.get("name") or x.get("key") or x.get("type") or x.get("label")
                                        for x in v] for k, v in ext.items() if v},
                    "adapters_unverified": [a["key"] for a in ext.get("payment_providers", []) +
                                            ext.get("couriers", [])],
                    "sandbox": (i.sandbox or {}).get("status")})
    return out


def update_available(db: Session, store_id) -> dict[str, str]:
    out = {}
    for inst in db.execute(select(PluginInstallation).where(PluginInstallation.store_id == store_id,
                                                            PluginInstallation.status != "uninstalled")).scalars():
        cat = catalog().get(inst.plugin_id)
        if cat and _newer(cat["version"], inst.version):
            out[inst.plugin_id] = cat["version"]
    return out


def _newer(a: str, b: str) -> bool:
    from .manifest import parse_version

    return parse_version(a) > parse_version(b)


