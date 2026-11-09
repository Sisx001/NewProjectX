"""Request principal resolution and permission dependencies."""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from .errors import AppError, Forbidden, Unauthorized
from .rbac import ALL, has_permission
from .security import token_hash
from .services import Actor, SettingDef, get_setting, setting

SESSION_COOKIE = "vt_session"
CSRF_COOKIE = "vt_csrf"

REQUIRE_STAFF_MFA: SettingDef = setting(
    key="security.require_staff_mfa", type="bool", default=False, group="Security",
    label="Require MFA for staff",
    description="Staff without an authenticator app can only enroll MFA until they finish setup.",
    write_permission="security.manage",
)


@dataclass
class Principal:
    user: object | None  # identity.models.User
    store: object  # platform.models.Store
    permissions: frozenset[str] = field(default_factory=frozenset)
    session: object | None = None
    api_key: object | None = None
    ip: str | None = None

    @property
    def is_staff(self) -> bool:
        return bool(self.user is not None and getattr(self.user, "kind", None) == "staff") or self.api_key is not None

    def can(self, perm: str) -> bool:
        return has_permission(self.permissions, perm)

    @property
    def actor(self) -> Actor:
        if self.api_key is not None:
            return Actor("api_key", self.api_key.id, f"API key {self.api_key.name}", self.ip)
        if self.user is None:
            return Actor("anonymous", None, None, self.ip)
        kind = "user" if self.user.kind == "staff" else "customer"
        return Actor(kind, self.user.id, self.user.email, self.ip)


def client_ip(request: Request) -> str | None:
    return getattr(request.state, "client_ip", None) or (request.client.host if request.client else None)


def current_store(db: Session):
    from ..modules.platform.models import Store

    store = db.execute(select(Store).order_by(Store.created_at).limit(1)).scalar_one_or_none()
    if store is None:
        raise AppError("not_installed", "VOLTORA has not been installed yet. Open /install.", status=503)
    return store


def store_dep(db: Session = Depends(get_db)):
    return current_store(db)


def _staff_permissions(db: Session, user, store) -> frozenset[str]:
    from ..modules.identity.models import Membership, Role

    row = db.execute(
        select(Membership, Role).join(Role, Role.id == Membership.role_id)
        .where(Membership.user_id == user.id, Membership.store_id == store.id)
    ).first()
    if row is None:
        return frozenset()
    m, role = row
    if m.expires_at is not None and m.expires_at <= utcnow():
        return frozenset()
    return frozenset(role.permissions or [])


def resolve_principal(request: Request, db: Session) -> Principal:
    from ..modules.identity.models import ApiKey, AuthSession, User

    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    store = current_store(db)
    ip = client_ip(request)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer vk_"):
        key = db.execute(select(ApiKey).where(ApiKey.key_hash == token_hash(auth[7:].strip()))).scalar_one_or_none()
        if key is None or key.revoked_at or (key.expires_at and key.expires_at <= utcnow()):
            raise Unauthorized("API key is invalid or revoked")
        key.last_used_at = utcnow()
        p = Principal(None, store, frozenset(key.permissions or []), api_key=key, ip=ip)
        request.state.principal = p
        return p

    token = request.cookies.get(SESSION_COOKIE)
    p = Principal(None, store, frozenset(), ip=ip)
    if token:
        sess = db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash(token))).scalar_one_or_none()
        if sess and not sess.revoked_at and sess.expires_at > utcnow():
            user = db.get(User, sess.user_id)
            if user and user.status == "active":
                perms: frozenset[str] = frozenset()
                if user.kind == "staff":
                    perms = _staff_permissions(db, user, store)
                    if user.mfa_enabled_at and not sess.mfa_verified or not user.mfa_enabled_at and get_setting(db, store.id, REQUIRE_STAFF_MFA.key):
                        perms = frozenset()
                if (utcnow() - sess.last_seen_at) > dt.timedelta(minutes=5):
                    sess.last_seen_at = utcnow()
                    db.commit()
                p = Principal(user, store, perms, session=sess, ip=ip)
    request.state.principal = p
    return p


def principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    return resolve_principal(request, db)


def signed_in(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = resolve_principal(request, db)
    if p.user is None and p.api_key is None:
        raise Unauthorized()
    return p


def _staff_gate(p: Principal, db: Session):
    if p.user is None and p.api_key is None:
        raise Unauthorized()
    if not p.is_staff:
        raise Forbidden("Staff access only")
    if p.user is not None and p.session is not None:
        if p.user.mfa_enabled_at and not p.session.mfa_verified:
            raise AppError("mfa_required", "Enter your authenticator code to continue", status=403)
        if not p.user.mfa_enabled_at and get_setting(db, p.store.id, REQUIRE_STAFF_MFA.key):
            raise AppError("mfa_enrollment_required", "Your store requires MFA. Set up an authenticator first.",
                           status=403)


def staff(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = resolve_principal(request, db)
    _staff_gate(p, db)
    return p


def require(*perms: str):
    """Dependency: staff principal holding every listed permission."""

    def dep(request: Request, db: Session = Depends(get_db)) -> Principal:
        p = resolve_principal(request, db)
        _staff_gate(p, db)
        missing = [x for x in perms if not p.can(x)]
        if missing:
            raise Forbidden(f"Missing permission: {', '.join(missing)}")
        return p

    dep.__name__ = "require_" + "_".join(x.replace(".", "_") for x in perms)
    return dep


def customer(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = resolve_principal(request, db)
    if p.user is None:
        raise Unauthorized()
    return p


def parse_uuid(value: str, what: str = "Resource") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        from .errors import NotFound

        raise NotFound(what) from exc


__all__ = ["Principal", "principal", "signed_in", "staff", "require", "customer", "store_dep", "current_store",
           "SESSION_COOKIE", "CSRF_COOKIE", "ALL", "client_ip", "parse_uuid"]
