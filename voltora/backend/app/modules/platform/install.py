"""First-run installer. Every call needs the one-time bootstrap token printed/written at boot; it is
invalidated when installation completes, after which the installer endpoints answer 410."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...config import CORE_VERSION, get_settings
from ...core import security as sec
from ...core.errors import AppError
from ...core.services import Actor, audit, limiter, set_settings
from ...db import get_db, utcnow
from .models import InstallState, Store

router = APIRouter(prefix="/api/install", tags=["install"])

STEPS = ["environment", "store", "owner", "catalog", "review"]


def bootstrap_path() -> Path:
    return get_settings().data_dir / "bootstrap-token"


def ensure_bootstrap_token(db: Session) -> str | None:
    """Called at startup. Returns a new raw token when one had to be generated."""
    st = db.get(InstallState, 1)
    if st and st.completed_at:
        return None
    if st and st.bootstrap_token_hash and bootstrap_path().exists():
        return None
    raw = sec.new_token(24)
    if st is None:
        st = InstallState(id=1, progress={})
        db.add(st)
    st.bootstrap_token_hash = sec.token_hash(raw)
    db.commit()
    path = bootstrap_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw + "\n")
    os.chmod(path, 0o600)
    return raw


def _state(db: Session) -> InstallState:
    st = db.get(InstallState, 1)
    if st is None:
        st = InstallState(id=1, progress={})
        db.add(st)
        db.flush()
    return st


def install_guard(request: Request, x_bootstrap_token: str | None = Header(default=None),
                  db: Session = Depends(get_db)) -> InstallState:
    ip = request.client.host if request.client else "?"
    limiter.hit(f"install:{ip}", 30, 60)
    st = _state(db)
    if st.completed_at:
        raise AppError("installed", "VOLTORA is already installed", status=410)
    if not x_bootstrap_token or not st.bootstrap_token_hash or not sec.consteq(
            sec.token_hash(x_bootstrap_token.strip()), st.bootstrap_token_hash):
        raise AppError("bootstrap_token_invalid",
                       f"Enter the setup token from {bootstrap_path()} on the server", status=401)
    return st


@router.get("/status")
def status(db: Session = Depends(get_db)):
    st = db.get(InstallState, 1)
    return {"installed": bool(st and st.completed_at), "steps": STEPS, "token_location": str(bootstrap_path()),
            "version": CORE_VERSION}


@router.post("/verify")
def verify(st: InstallState = Depends(install_guard)):
    return {"ok": True, "progress": st.progress}


@router.get("/checks")
def checks(st: InstallState = Depends(install_guard), db: Session = Depends(get_db)):
    s = get_settings()
    out = []
    pgv = db.execute(text("show server_version")).scalar()
    out.append({"key": "database", "label": "PostgreSQL connection", "ok": True, "detail": f"Server {pgv}"})
    major = int(pgv.split(".")[0])
    out.append({"key": "database_version", "label": "PostgreSQL ≥ 14", "ok": major >= 14, "detail": pgv})
    out.append({"key": "python", "label": "Python ≥ 3.11", "ok": sys.version_info >= (3, 11),
                "detail": sys.version.split()[0]})
    s.data_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(s.data_dir).free
    out.append({"key": "disk", "label": "Free disk ≥ 2 GB", "ok": free > 2 * 1024 ** 3,
                "detail": f"{free // 1024 ** 3} GB free in {s.data_dir}"})
    out.append({"key": "secret_key", "label": "Secret key configured",
                "ok": s.env == "development" or len(s.secret_key) >= 32,
                "detail": "development default in use" if s.env == "development" else "set"})
    out.append({"key": "vault_key", "label": "Vault key configured", "ok": bool(s.vault_key) or s.env == "development",
                "detail": "set" if s.vault_key else "derived from secret key (development only)"})
    out.append({"key": "https", "label": "Public URL uses HTTPS", "ok": s.public_url.startswith("https://"),
                "detail": s.public_url, "warning_only": True})
    out.append({"key": "payments_mode", "label": "Payments mode", "ok": True,
                "detail": f"{s.payments_mode} (live gateways require verified credentials)"})
    pg_dump = shutil.which("pg_dump")
    out.append({"key": "pg_dump", "label": "pg_dump available for backups", "ok": bool(pg_dump),
                "detail": pg_dump or "install postgresql-client", "warning_only": True})
    st.progress = {**st.progress, "environment": {"done": all(c["ok"] or c.get("warning_only") for c in out)}}
    db.commit()
    return {"checks": out}


class StoreIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    locale: str = Field(default="en", max_length=10)
    timezone: str = Field(default="Asia/Dhaka", max_length=64)
    support_email: EmailStr | None = None
    support_phone: str | None = Field(default=None, max_length=32)


@router.post("/store")
def save_store(body: StoreIn, st: InstallState = Depends(install_guard), db: Session = Depends(get_db)):
    from ..identity.api import ensure_builtin_roles

    store = db.execute(select(Store).limit(1)).scalar_one_or_none()
    slug = "".join(c if c.isalnum() else "-" for c in body.name.lower()).strip("-")[:60] or "store"
    if store is None:
        store = Store(name=body.name, slug=slug, currency=body.currency.upper(), locale=body.locale,
                      timezone=body.timezone, environment=get_settings().env)
        db.add(store)
        db.flush()
    else:
        store.name, store.currency, store.locale, store.timezone = body.name, body.currency.upper(), body.locale, \
            body.timezone
    ensure_builtin_roles(db, store.id)
    changes = {"store.support_email": body.support_email or "", "store.support_phone": body.support_phone or ""}
    set_settings(db, store.id, changes, Actor("system", label="installer"))
    st.progress = {**st.progress, "store": {"done": True, "store_id": str(store.id)}}
    db.commit()
    return {"ok": True, "progress": st.progress}


class OwnerIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=160)
    password: str = Field(max_length=200)


@router.post("/owner")
def create_owner(body: OwnerIn, st: InstallState = Depends(install_guard), db: Session = Depends(get_db)):
    from ..identity.api import create_user, ensure_builtin_roles
    from ..identity.models import Membership, User

    store = db.execute(select(Store).limit(1)).scalar_one_or_none()
    if store is None:
        raise AppError("store_missing", "Complete the store step first")
    roles = ensure_builtin_roles(db, store.id)
    existing = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if existing:
        if existing.kind != "staff":
            raise AppError("email_taken", "That email belongs to a customer account")
        problems = sec.password_problems(body.password)
        if problems:
            raise AppError("weak_password", problems[0])
        existing.password_hash = sec.hash_password(body.password)
        existing.name = body.name
        u = existing
    else:
        u = create_user(db, email=body.email, password=body.password, name=body.name, kind="staff")
    u.email_verified_at = utcnow()
    if not db.execute(select(Membership).where(Membership.user_id == u.id)).first():
        db.add(Membership(user_id=u.id, store_id=store.id, role_id=roles["owner"].id))
    st.progress = {**st.progress, "owner": {"done": True, "email": u.email}}
    audit(db, store.id, Actor("system", label="installer"), "install.owner_created", "user", u.id)
    db.commit()
    return {"ok": True, "progress": st.progress}


class CatalogIn(BaseModel):
    mode: str = Field(pattern="^(empty|demo)$")


@router.post("/catalog")
def catalog(body: CatalogIn, st: InstallState = Depends(install_guard), db: Session = Depends(get_db)):
    store = db.execute(select(Store).limit(1)).scalar_one_or_none()
    if store is None:
        raise AppError("store_missing", "Complete the store step first")
    from ...seed import seed_foundation

    result = seed_foundation(db, store, demo=body.mode == "demo")
    st.progress = {**st.progress, "catalog": {"done": True, "mode": body.mode, "result": result}}
    db.commit()
    return {"ok": True, "result": result, "progress": st.progress}


@router.post("/complete")
def complete(response: Response, st: InstallState = Depends(install_guard), db: Session = Depends(get_db)):
    missing = [s for s in ("store", "owner") if not st.progress.get(s, {}).get("done")]
    if missing:
        raise AppError("incomplete", f"Finish these steps first: {', '.join(missing)}")
    store = db.execute(select(Store).limit(1)).scalar_one()
    if not st.progress.get("catalog", {}).get("done"):
        from ...seed import seed_foundation

        seed_foundation(db, store, demo=False)
    st.completed_at = utcnow()
    st.bootstrap_token_hash = None
    audit(db, store.id, Actor("system", label="installer"), "install.completed", "store", store.id)
    db.commit()
    try:
        bootstrap_path().unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True, "admin_url": "/admin"}
