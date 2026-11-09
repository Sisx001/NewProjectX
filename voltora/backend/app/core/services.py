"""Cross-cutting services: audit chain, outbox events, settings registry, idempotency, rate limits."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db import utcnow
from .errors import AppError, Conflict

log = logging.getLogger("voltora")


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


# ---------------------------------------------------------------- audit
REDACT_KEYS = {"password", "secret", "token", "api_key", "apikey", "authorization", "otp", "code", "card", "cvv"}


def redact(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: ("[redacted]" if any(r in k.lower() for r in REDACT_KEYS) else redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


@dataclass
class Actor:
    kind: str = "system"  # user|system|ai|plugin|api_key|customer
    id: uuid.UUID | None = None
    label: str | None = None
    ip: str | None = None


SYSTEM = Actor()


def audit(db: Session, store_id: uuid.UUID | None, actor: Actor, action: str, target_type: str | None = None,
          target_id: Any = None, data: dict | None = None) -> None:
    from ..modules.platform.models import AuditEvent

    # Serialise chain appends per store inside the caller's transaction.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"audit:{store_id}"})
    prev = db.execute(
        select(AuditEvent.hash).where(AuditEvent.store_id == store_id).order_by(AuditEvent.seq.desc()).limit(1)
    ).scalar()
    prev = prev or "0" * 64
    clean = redact(data or {})
    created = utcnow()
    body = _canon([str(store_id), actor.kind, str(actor.id), action, target_type, str(target_id), clean,
                   created.isoformat(), prev])
    db.add(AuditEvent(store_id=store_id, actor_id=actor.id, actor_kind=actor.kind, actor_label=actor.label,
                      action=action, target_type=target_type, target_id=str(target_id) if target_id else None,
                      data=clean, ip=actor.ip, prev_hash=prev, hash=hashlib.sha256(body.encode()).hexdigest(),
                      created_at=created))


def verify_audit_chain(db: Session, store_id: uuid.UUID | None) -> dict:
    from ..modules.platform.models import AuditEvent

    prev = "0" * 64
    n = 0
    for ev in db.execute(select(AuditEvent).where(AuditEvent.store_id == store_id).order_by(AuditEvent.seq)).scalars():
        body = _canon([str(ev.store_id), ev.actor_kind, str(ev.actor_id), ev.action, ev.target_type,
                       str(ev.target_id), ev.data, ev.created_at.isoformat(), ev.prev_hash])
        if ev.prev_hash != prev or hashlib.sha256(body.encode()).hexdigest() != ev.hash:
            return {"ok": False, "checked": n, "broken_at": ev.seq}
        prev = ev.hash
        n += 1
    return {"ok": True, "checked": n}


# ---------------------------------------------------------------- outbox events
Handler = Callable[[Session, Any], dict | None]
_handlers: dict[str, list[tuple[str, Handler]]] = defaultdict(list)
EVENT_TYPES: dict[str, str] = {}  # type -> description (feeds automation triggers and AI capability graph)


def event_type(name: str, description: str) -> str:
    EVENT_TYPES[name] = description
    return name


def on(event: str, name: str | None = None):
    """Register an outbox subscriber. `event` may be '*' for all events."""

    def deco(fn: Handler):
        _handlers[event].append((name or f"{fn.__module__}.{fn.__name__}", fn))
        return fn

    return deco


def emit(db: Session, store_id: uuid.UUID | None, type_: str, payload: dict, dedupe_key: str | None = None) -> None:
    from ..modules.platform.models import OutboxEvent

    db.add(OutboxEvent(store_id=store_id, type=type_, payload=json.loads(_canon(payload)), dedupe_key=dedupe_key))


MAX_ATTEMPTS = 6


def process_outbox(session_factory, limit: int = 50) -> int:
    """Deliver pending events. Each subscriber result is recorded so retries skip handlers that already succeeded."""
    from ..modules.platform.models import OutboxEvent

    done = 0
    with session_factory() as db:
        rows = db.execute(
            select(OutboxEvent).where(OutboxEvent.status.in_(["pending", "failed"]),
                                      OutboxEvent.next_attempt_at <= utcnow())
            .order_by(OutboxEvent.created_at).limit(limit).with_for_update(skip_locked=True)
        ).scalars().all()
        for ev in rows:
            results = dict(ev.results or {})
            errors = []
            for name, fn in _handlers.get(ev.type, []) + _handlers.get("*", []):
                if results.get(name, {}).get("ok"):
                    continue
                try:
                    with db.begin_nested():
                        out = fn(db, ev)
                    results[name] = {"ok": True, "result": out}
                except Exception as exc:  # noqa: BLE001 - recorded for diagnostics
                    log.exception("outbox handler %s failed for %s", name, ev.type)
                    results[name] = {"ok": False, "error": str(exc)[:500]}
                    errors.append(f"{name}: {exc}")
            ev.results = results
            ev.attempts += 1
            if errors:
                ev.last_error = "; ".join(errors)[:2000]
                ev.status = "dead" if ev.attempts >= MAX_ATTEMPTS else "failed"
                ev.next_attempt_at = utcnow() + dt.timedelta(seconds=min(3600, 5 * 4 ** ev.attempts))
            else:
                ev.status = "done"
                ev.processed_at = utcnow()
                done += 1
        db.commit()
    return done


class OutboxWorker(threading.Thread):
    def __init__(self, session_factory, interval: float = 1.0):
        super().__init__(daemon=True, name="voltora-outbox")
        self.sf = session_factory
        self.interval = interval
        self.stop_event = threading.Event()
        self.last_tick: dt.datetime | None = None

    def run(self):
        while not self.stop_event.is_set():
            try:
                process_outbox(self.sf)
                for job in list(PERIODIC_JOBS):
                    job.maybe_run(self.sf)
                self.last_tick = utcnow()
            except Exception:  # noqa: BLE001
                log.exception("outbox worker tick failed")
            self.stop_event.wait(self.interval)


@dataclass
class PeriodicJob:
    name: str
    every_seconds: int
    fn: Callable[[Session], Any]
    last_run: float = 0.0
    last_error: str | None = None
    last_result: Any = None

    def maybe_run(self, sf):
        if time.monotonic() - self.last_run < self.every_seconds:
            return
        self.last_run = time.monotonic()
        try:
            with sf() as db:
                self.last_result = self.fn(db)
                db.commit()
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)[:500]
            log.exception("periodic job %s failed", self.name)


PERIODIC_JOBS: list[PeriodicJob] = []


def periodic(name: str, every_seconds: int):
    def deco(fn):
        PERIODIC_JOBS.append(PeriodicJob(name, every_seconds, fn))
        return fn

    return deco


# ---------------------------------------------------------------- settings registry
@dataclass
class SettingDef:
    key: str
    type: str  # string|text|bool|int|money|enum|json|color|email|url
    default: Any
    label: str
    group: str
    description: str = ""
    choices: list[str] | None = None
    min: int | None = None
    max: int | None = None
    read_permission: str = "settings.read"
    write_permission: str = "settings.write"
    public: bool = False  # exposed to the storefront config endpoint


SETTINGS: dict[str, SettingDef] = {}


def setting(**kw) -> SettingDef:
    d = SettingDef(**kw)
    if d.key in SETTINGS:
        raise RuntimeError(f"Duplicate setting definition {d.key}")  # one canonical home per setting
    SETTINGS[d.key] = d
    return d


def coerce_setting(d: SettingDef, value: Any) -> Any:
    try:
        if d.type == "bool":
            if not isinstance(value, bool):
                raise ValueError("expected true/false")
        elif d.type in ("int", "money"):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("expected a whole number")
            if d.min is not None and value < d.min:
                raise ValueError(f"minimum is {d.min}")
            if d.max is not None and value > d.max:
                raise ValueError(f"maximum is {d.max}")
        elif d.type == "enum":
            if value not in (d.choices or []):
                raise ValueError(f"choose one of {', '.join(d.choices or [])}")
        elif d.type in ("string", "text", "email", "url", "color"):
            if not isinstance(value, str):
                raise ValueError("expected text")
            if d.max is not None and len(value) > d.max:
                raise ValueError(f"at most {d.max} characters")
            if d.type == "email" and value and "@" not in value:
                raise ValueError("expected an email address")
            if d.type == "url" and value and not value.startswith(("https://", "http://", "/")):
                raise ValueError("expected a URL")
            if d.type == "color" and value and not (value.startswith("#") and len(value) in (4, 7, 9)):
                raise ValueError("expected a hex color")
        elif d.type == "json":
            json.dumps(value)
    except ValueError as exc:
        raise AppError("invalid_setting", f"{d.label}: {exc}", details={"key": d.key}) from exc
    return value


def get_setting(db: Session, store_id: uuid.UUID, key: str) -> Any:
    from ..modules.platform.models import SettingValue

    d = SETTINGS[key]
    row = db.get(SettingValue, (store_id, key))
    return row.value if row is not None else d.default


def set_settings(db: Session, store_id: uuid.UUID, changes: dict[str, Any], actor: Actor, note: str | None = None,
                 can: Callable[[str], bool] = lambda p: True) -> dict:
    from ..modules.platform.models import ConfigRevision, SettingValue

    diff = {}
    for key, value in changes.items():
        d = SETTINGS.get(key)
        if d is None:
            raise AppError("unknown_setting", f"Unknown setting {key}")
        if not can(d.write_permission):
            raise AppError("forbidden", f"Not allowed to change {d.label}", status=403)
        value = coerce_setting(d, value)
        row = db.get(SettingValue, (store_id, key))
        old = row.value if row else d.default
        if old == value:
            continue
        if row is None:
            db.add(SettingValue(store_id=store_id, key=key, value=value, updated_by=actor.id))
        else:
            row.value = value
            row.revision += 1
            row.updated_by = actor.id
        diff[key] = {"from": old, "to": value}
    if diff:
        db.add(ConfigRevision(store_id=store_id, changes=diff, actor_id=actor.id, note=note))
        audit(db, store_id, actor, "settings.update", "settings", None, {"keys": sorted(diff)})
    return diff


# ---------------------------------------------------------------- idempotency
def request_fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canon(payload).encode()).hexdigest()


def idempotent_lookup(db: Session, scope: str, key: str, payload: Any) -> dict | None:
    """Returns the stored response for a replayed key; raises on key reuse with a different payload."""
    from sqlalchemy.dialects.postgresql import insert

    from ..modules.platform.models import IdempotencyRecord

    fp = request_fingerprint(payload)
    db.execute(insert(IdempotencyRecord).values(scope=scope, key=key, request_hash=fp, created_at=utcnow())
               .on_conflict_do_nothing())
    rec = db.execute(select(IdempotencyRecord).where(IdempotencyRecord.scope == scope, IdempotencyRecord.key == key)
                     .with_for_update()).scalar_one()
    if rec.request_hash != fp:
        raise Conflict("idempotency_mismatch", "This idempotency key was already used with a different request")
    return rec.response


def idempotent_store(db: Session, scope: str, key: str, response: dict, status_code: int = 200) -> None:
    from ..modules.platform.models import IdempotencyRecord

    rec = db.get(IdempotencyRecord, (scope, key))
    if rec:
        rec.response = json.loads(_canon(response))
        rec.status_code = status_code


# ---------------------------------------------------------------- rate limit
class RateLimiter:
    """In-process sliding window. Adequate for a single-node install; documented as such in ops docs."""

    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()
        self.blocked: dict[str, int] = defaultdict(int)

    def hit(self, key: str, limit: int, window: float) -> None:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                self.blocked[key.split(":")[0]] += 1
                raise AppError("rate_limited", "Too many attempts. Please wait and try again.", status=429,
                               details={"retry_after": int(window - (now - q[0])) + 1})
            q.append(now)

    def reset(self):
        with self.lock:
            self.hits.clear()


limiter = RateLimiter()


@dataclass
class Page:
    items: list
    total: int
    page: int
    page_size: int
    extra: dict = field(default_factory=dict)

    def dump(self):
        return {"items": self.items, "total": self.total, "page": self.page, "page_size": self.page_size, **self.extra}
