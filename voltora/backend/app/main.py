from __future__ import annotations

import importlib
import logging
import pkgutil
import queue
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import capabilities
from .config import CORE_VERSION, get_settings
from .core.deps import CSRF_COOKIE, SESSION_COOKIE
from .core.errors import AppError, app_error_handler, validation_error_handler
from .core.security import consteq
from .core.services import OutboxWorker, redact
from .db import session_factory

log = logging.getLogger("voltora")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT = ("/api/payments/webhooks/", "/api/shipping/webhooks/", "/api/install/", "/api/plugins/webhooks/")
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
_request_id: threading.local = threading.local()


def discover_modules():
    """Import every app.modules.<name>.{models,service,api}; return routers. No central registry to edit."""
    from . import modules

    routers = []
    for info in sorted(pkgutil.iter_modules(modules.__path__), key=lambda m: m.name):
        for part in ("models", "service", "tools", "api"):
            try:
                mod = importlib.import_module(f"app.modules.{info.name}.{part}")
            except ModuleNotFoundError as exc:
                if exc.name != f"app.modules.{info.name}.{part}":
                    raise
                continue
            if part == "api":
                for r in [getattr(mod, "router", None), *getattr(mod, "extra_routers", [])]:
                    if r is not None:
                        routers.append(r)
                meta = getattr(mod, "MODULE", {"title": info.name.title()})
                capabilities.MODULES[info.name] = meta
    return routers


class DbLogHandler(logging.Handler):
    """Ships WARNING+ (and `voltora.audit`) records to the log_records table off the request path."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.q: queue.Queue = queue.Queue(maxsize=5000)
        threading.Thread(target=self._drain, daemon=True, name="voltora-log").start()

    def emit(self, record):
        if record.levelno < logging.WARNING and not record.name.startswith("voltora.http"):
            return
        if record.name.startswith("sqlalchemy"):
            return
        try:
            msg = record.getMessage()
            msg = re.sub(r"(?i)(password|secret|token|api[_-]?key)=\S+", r"\1=[redacted]", msg)
            self.q.put_nowait({"level": record.levelname, "logger": record.name[:80], "message": msg[:4000],
                               "request_id": getattr(_request_id, "value", None),
                               "data": redact(getattr(record, "data", {}) or {})})
        except queue.Full:
            pass

    def _drain(self):
        from .modules.platform.models import LogRecord

        while True:
            item = self.q.get()
            try:
                with session_factory()() as db:
                    db.add(LogRecord(**item))
                    db.commit()
            except Exception:  # noqa: BLE001 - never let logging crash the app
                pass


def client_ip_from(request: Request, trusted: list[str]) -> str | None:
    peer = request.client.host if request.client else None
    xff = request.headers.get("x-forwarded-for")
    if peer in trusted and xff:
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        for hop in reversed(hops):
            if hop not in trusted:
                return hop
    return peer


def create_app(start_worker: bool | None = None) -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=s.log_level)
    routers = discover_modules()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .modules.platform.install import ensure_bootstrap_token

        with session_factory()() as db:
            try:
                raw = ensure_bootstrap_token(db)
                if raw:
                    log.warning("VOLTORA is not installed. Setup token written to %s",
                                (s.data_dir / "bootstrap-token").resolve())
            except Exception:  # noqa: BLE001 - schema may not exist yet (run migrations)
                log.exception("Could not prepare installer state; did you run `alembic upgrade head`?")
        run = s.run_worker_in_process if start_worker is None else start_worker
        if run:
            app.state.worker = OutboxWorker(session_factory())
            app.state.worker.start()
        yield
        if getattr(app.state, "worker", None):
            app.state.worker.stop_event.set()

    app = FastAPI(title="VOLTORA API", version=CORE_VERSION, lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    if not any(isinstance(h, DbLogHandler) for h in logging.getLogger().handlers) and s.env != "test":
        logging.getLogger().addHandler(DbLogHandler())

    @app.middleware("http")
    async def security_mw(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        _request_id.value = rid
        request.state.request_id = rid
        request.state.client_ip = client_ip_from(request, s.trusted_proxies)
        path = request.url.path
        if request.method in UNSAFE and path.startswith("/api/") and not path.startswith(CSRF_EXEMPT):
            origin = request.headers.get("origin")
            # Same-origin is judged against the Host the browser used (X-Forwarded-Host only from trusted proxies).
            host = request.headers.get("host", "")
            if request.client and request.client.host in s.trusted_proxies:
                host = request.headers.get("x-forwarded-host", host).split(",")[0].strip()
            origin_host = origin.split("://", 1)[-1] if origin else ""
            if origin and origin not in s.allowed_origins and origin_host != host:
                return JSONResponse({"error": {"code": "bad_origin", "message": "Cross-site request blocked"}},
                                    status_code=403)
            has_bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
            if request.cookies.get(SESSION_COOKIE) and not has_bearer:
                sent = request.headers.get("x-csrf-token", "")
                cookie = request.cookies.get(CSRF_COOKIE, "")
                if not sent or not cookie or not consteq(sent, cookie):
                    return JSONResponse({"error": {"code": "csrf_failed",
                                                   "message": "Security token missing or expired. Reload the page."}},
                                        status_code=403)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("Unhandled error on %s %s", request.method, path)
            response = JSONResponse({"error": {"code": "internal_error", "message": "Something went wrong",
                                               "details": {"request_id": rid}}}, status_code=500)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    capabilities.ROUTERS[:] = routers
    for r in routers:
        app.include_router(r)

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"error": {"code": "not_found", "message": "Not found"}}, status_code=404)
            f = (WEB_DIST / full_path).resolve()
            if full_path and f.is_file() and WEB_DIST in f.parents:
                return FileResponse(f)
            return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app() if get_settings().env != "test" else None
