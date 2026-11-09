from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core import security as sec
from ...core.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    Principal,
    client_ip,
    current_store,
    parse_uuid,
    require,
    resolve_principal,
    signed_in,
)
from ...core.errors import AppError, Conflict, Forbidden, NotFound, Unauthorized
from ...core.rbac import ALL, BUILTIN_ROLES, PERMISSIONS, validate_permissions
from ...core.services import Actor, audit, emit, event_type, limiter
from ...db import get_db, utcnow
from .models import ApiKey, AuthSession, Membership, OneTimeToken, RecoveryCode, Role, User

router = APIRouter(prefix="/api", tags=["identity"])

EV_CUSTOMER_REGISTERED = event_type("customer.registered", "A shopper created an account")
EV_STAFF_INVITED = event_type("staff.invited", "A staff member was invited")


# ------------------------------------------------------------------ helpers
def user_out(u: User) -> dict:
    return {"id": str(u.id), "email": u.email, "name": u.name, "phone": u.phone, "kind": u.kind, "status": u.status,
            "mfa_enabled": bool(u.mfa_enabled_at), "email_verified": bool(u.email_verified_at),
            "locale": u.locale, "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}


def _set_session_cookies(resp: Response, token: str, days: int) -> None:
    s = get_settings()
    resp.set_cookie(SESSION_COOKIE, token, max_age=days * 86400, httponly=True, secure=s.cookie_secure,
                    samesite="lax", path="/")
    resp.set_cookie(CSRF_COOKIE, sec.new_token(18), max_age=days * 86400, httponly=False, secure=s.cookie_secure,
                    samesite="lax", path="/")


def start_session(db: Session, resp: Response, request: Request, user: User, mfa_verified: bool) -> AuthSession:
    s = get_settings()
    token = sec.new_token()
    sess = AuthSession(user_id=user.id, token_hash=sec.token_hash(token), mfa_verified=mfa_verified,
                       ip=client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:300],
                       expires_at=utcnow() + dt.timedelta(days=s.session_days))
    db.add(sess)
    user.last_login_at = utcnow()
    _set_session_cookies(resp, token, s.session_days)
    return sess


def create_user(db: Session, *, email: str, password: str | None, name: str, kind: str, status: str = "active",
                phone: str | None = None) -> User:
    email = email.strip().lower()
    if db.execute(select(User.id).where(User.email == email)).first():
        raise Conflict("email_taken", "An account with this email already exists")
    if password is not None:
        problems = sec.password_problems(password)
        if problems:
            raise AppError("weak_password", problems[0], details=problems)
    u = User(email=email, name=name.strip(), kind=kind, status=status, phone=phone,
             password_hash=sec.hash_password(password) if password else None)
    db.add(u)
    db.flush()
    return u


def ensure_builtin_roles(db: Session, store_id: uuid.UUID) -> dict[str, Role]:
    roles = {r.key: r for r in db.execute(select(Role).where(Role.store_id == store_id)).scalars()}
    for key, spec in BUILTIN_ROLES.items():
        if key not in roles:
            r = Role(store_id=store_id, key=key, name=spec["name"], description=spec["description"],
                     permissions=list(spec["permissions"]), builtin=True)
            db.add(r)
            roles[key] = r
        else:
            roles[key].permissions = list(spec["permissions"])  # keep built-ins in sync with the catalog
    db.flush()
    return roles


def me_payload(p: Principal) -> dict:
    u = p.user
    return {
        "user": user_out(u) if u else None,
        "permissions": sorted(p.permissions),
        "mfa_pending": bool(u and u.kind == "staff" and u.mfa_enabled_at and p.session and not p.session.mfa_verified),
        "store": {"id": str(p.store.id), "name": p.store.name, "currency": p.store.currency,
                  "locale": p.store.locale, "timezone": p.store.timezone, "environment": p.store.environment},
    }


# ------------------------------------------------------------------ auth
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    marketing_consent: bool = False


@router.post("/auth/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    limiter.hit(f"register:{client_ip(request)}", 10, 3600)
    store = current_store(db)
    u = create_user(db, email=body.email, password=body.password, name=body.name, kind="customer", phone=body.phone)
    u.marketing_consent = body.marketing_consent
    start_session(db, response, request, u, mfa_verified=True)
    emit(db, store.id, EV_CUSTOMER_REGISTERED, {"user_id": str(u.id), "email": u.email})
    audit(db, store.id, Actor("customer", u.id, u.email, client_ip(request)), "customer.register", "user", u.id)
    db.commit()
    return {"user": user_out(u)}


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


LOCK_AFTER = 8


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    email = body.email.lower()
    limiter.hit(f"login-ip:{ip}", 30, 60)
    limiter.hit(f"login-email:{email}", 10, 600)
    store = current_store(db)
    u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if u and u.locked_until and u.locked_until > utcnow():
        raise AppError("account_locked", "Too many failed attempts. Try again in a few minutes.", status=423)
    if not u or u.status != "active" or not sec.verify_password(u.password_hash if u else None, body.password):
        if u:
            u.failed_logins += 1
            if u.failed_logins >= LOCK_AFTER:
                u.locked_until = utcnow() + dt.timedelta(minutes=15)
                u.failed_logins = 0
                audit(db, store.id, Actor("system", ip=ip), "auth.locked", "user", u.id)
            db.commit()
        raise Unauthorized("Email or password is incorrect")
    u.failed_logins = 0
    sess = start_session(db, response, request, u, mfa_verified=not u.mfa_enabled_at)
    audit(db, store.id, Actor("user" if u.kind == "staff" else "customer", u.id, u.email, ip), "auth.login",
          "user", u.id, {"mfa_pending": not sess.mfa_verified})
    db.commit()
    return {"user": user_out(u), "mfa_required": not sess.mfa_verified}


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


@router.post("/auth/mfa/challenge")
def mfa_challenge(body: CodeIn, request: Request, db: Session = Depends(get_db), p: Principal = Depends(signed_in)):
    limiter.hit(f"mfa:{p.user.id}", 8, 300)
    u = p.user
    if not u.mfa_enabled_at:
        raise AppError("mfa_not_enabled", "MFA is not enabled for this account")
    ok = sec.verify_totp(sec.unseal(u.mfa_secret_sealed), body.code)
    used_recovery = False
    if not ok:
        rc = db.execute(select(RecoveryCode).where(RecoveryCode.user_id == u.id, RecoveryCode.used_at.is_(None),
                                                   RecoveryCode.code_hash == sec.token_hash(body.code.strip()))
                        ).scalar_one_or_none()
        if rc:
            rc.used_at = utcnow()
            ok = used_recovery = True
    if not ok:
        raise AppError("invalid_code", "That code is not valid", status=400)
    p.session.mfa_verified = True
    audit(db, p.store.id, p.actor, "auth.mfa_verified", "user", u.id, {"recovery_code": used_recovery})
    db.commit()
    request.state.principal = None
    return me_payload(resolve_principal(request, db))


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(update(AuthSession).where(AuthSession.token_hash == sec.token_hash(token))
                   .values(revoked_at=utcnow()))
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request, db: Session = Depends(get_db)):
    p = resolve_principal(request, db)
    return me_payload(p)


@router.post("/auth/mfa/setup")
def mfa_setup(db: Session = Depends(get_db), p: Principal = Depends(signed_in)):
    if p.user.mfa_enabled_at:
        raise Conflict("mfa_enabled", "MFA is already enabled")
    secret = sec.new_totp_secret()
    p.user.mfa_pending_sealed = sec.seal(secret)
    db.commit()
    return {"secret": secret, "otpauth_uri": sec.totp_uri(secret, p.user.email, p.store.name)}


@router.post("/auth/mfa/enable")
def mfa_enable(body: CodeIn, db: Session = Depends(get_db), p: Principal = Depends(signed_in)):
    u = p.user
    if not u.mfa_pending_sealed:
        raise AppError("mfa_setup_missing", "Start MFA setup first")
    secret = sec.unseal(u.mfa_pending_sealed)
    if not sec.verify_totp(secret, body.code):
        raise AppError("invalid_code", "That code is not valid")
    u.mfa_secret_sealed, u.mfa_pending_sealed, u.mfa_enabled_at = u.mfa_pending_sealed, None, utcnow()
    codes = sec.new_recovery_codes()
    db.query(RecoveryCode).filter(RecoveryCode.user_id == u.id).delete()
    for c in codes:
        db.add(RecoveryCode(user_id=u.id, code_hash=sec.token_hash(c)))
    p.session.mfa_verified = True
    audit(db, p.store.id, p.actor, "auth.mfa_enabled", "user", u.id)
    db.commit()
    return {"recovery_codes": codes}


class DisableMfaIn(BaseModel):
    password: str
    code: str


@router.post("/auth/mfa/disable")
def mfa_disable(body: DisableMfaIn, db: Session = Depends(get_db), p: Principal = Depends(signed_in)):
    u = p.user
    if not u.mfa_enabled_at:
        return {"ok": True}
    if not sec.verify_password(u.password_hash, body.password) or not sec.verify_totp(
            sec.unseal(u.mfa_secret_sealed), body.code):
        raise AppError("invalid_credentials", "Password or code is incorrect")
    u.mfa_secret_sealed = None
    u.mfa_enabled_at = None
    audit(db, p.store.id, p.actor, "auth.mfa_disabled", "user", u.id)
    db.commit()
    return {"ok": True}


@router.get("/auth/sessions")
def list_sessions(db: Session = Depends(get_db), p: Principal = Depends(signed_in)):
    rows = db.execute(select(AuthSession).where(AuthSession.user_id == p.user.id, AuthSession.revoked_at.is_(None),
                                                AuthSession.expires_at > utcnow())
                      .order_by(AuthSession.last_seen_at.desc())).scalars()
    return {"items": [{"id": str(s.id), "ip": s.ip, "user_agent": s.user_agent, "created_at": s.created_at.isoformat(),
                       "last_seen_at": s.last_seen_at.isoformat(), "current": s.id == p.session.id} for s in rows]}


@router.delete("/auth/sessions/{session_id}")
def revoke_session(session_id: str, db: Session = Depends(get_db), p: Principal = Depends(signed_in)):
    sid = parse_uuid(session_id, "Session")
    n = db.execute(update(AuthSession).where(AuthSession.id == sid, AuthSession.user_id == p.user.id)
                   .values(revoked_at=utcnow())).rowcount
    if not n:
        raise NotFound("Session")
    audit(db, p.store.id, p.actor, "auth.session_revoked", "session", sid)
    db.commit()
    return {"ok": True}


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(max_length=200)


@router.post("/auth/password/change")
def change_password(body: PasswordChangeIn, db: Session = Depends(get_db), p: Principal = Depends(signed_in)):
    u = p.user
    if not sec.verify_password(u.password_hash, body.current_password):
        raise AppError("invalid_credentials", "Current password is incorrect")
    problems = sec.password_problems(body.new_password)
    if problems:
        raise AppError("weak_password", problems[0])
    u.password_hash = sec.hash_password(body.new_password)
    # Rotate: revoke every other session.
    db.execute(update(AuthSession).where(AuthSession.user_id == u.id, AuthSession.id != p.session.id,
                                         AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit(db, p.store.id, p.actor, "auth.password_changed", "user", u.id)
    db.commit()
    return {"ok": True}


def issue_token(db: Session, user: User, purpose: str, hours: int) -> str:
    raw = sec.new_token()
    db.add(OneTimeToken(user_id=user.id, purpose=purpose, token_hash=sec.token_hash(raw),
                        expires_at=utcnow() + dt.timedelta(hours=hours)))
    return raw


def consume_token(db: Session, raw: str, purpose: str) -> User:
    t = db.execute(select(OneTimeToken).where(OneTimeToken.token_hash == sec.token_hash(raw),
                                              OneTimeToken.purpose == purpose).with_for_update()).scalar_one_or_none()
    if t is None or t.used_at is not None or t.expires_at <= utcnow():
        raise AppError("invalid_token", "This link is invalid or has expired")
    t.used_at = utcnow()
    return db.get(User, t.user_id)


class ForgotIn(BaseModel):
    email: EmailStr


@router.post("/auth/password/forgot")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    from ..messaging.service import queue_message

    limiter.hit(f"forgot:{client_ip(request)}", 5, 900)
    store = current_store(db)
    u = db.execute(select(User).where(User.email == body.email.lower(), User.status == "active")).scalar_one_or_none()
    if u:
        raw = issue_token(db, u, "password_reset", 2)
        link = f"{get_settings().public_url}/account/reset?token={raw}"
        queue_message(db, store.id, channel="email", to=u.email, template="auth.password_reset",
                      context={"name": u.name or u.email, "link": link}, sensitive=True)
        db.commit()
    return {"ok": True, "message": "If an account exists for that email, a reset link is on its way."}


class ResetIn(BaseModel):
    token: str
    password: str = Field(max_length=200)


@router.post("/auth/password/reset")
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    store = current_store(db)
    u = consume_token(db, body.token, "password_reset")
    problems = sec.password_problems(body.password)
    if problems:
        raise AppError("weak_password", problems[0])
    u.password_hash = sec.hash_password(body.password)
    u.failed_logins = 0
    u.locked_until = None
    db.execute(update(AuthSession).where(AuthSession.user_id == u.id).values(revoked_at=utcnow()))
    audit(db, store.id, Actor("customer" if u.kind == "customer" else "user", u.id, u.email), "auth.password_reset",
          "user", u.id)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ staff & roles
def role_out(r: Role, members: int = 0) -> dict:
    return {"id": str(r.id), "key": r.key, "name": r.name, "description": r.description,
            "permissions": r.permissions, "builtin": r.builtin, "members": members}


@router.get("/staff/permissions")
def permission_catalog(p: Principal = Depends(require("staff.read"))):
    groups: dict[str, list] = {}
    for key, desc in PERMISSIONS.items():
        groups.setdefault(key.split(".")[0], []).append({"key": key, "description": desc})
    return {"groups": groups}


@router.get("/staff/roles")
def list_roles(db: Session = Depends(get_db), p: Principal = Depends(require("staff.read"))):
    counts = dict(db.execute(select(Membership.role_id, func.count()).where(Membership.store_id == p.store.id)
                             .group_by(Membership.role_id)).all())
    roles = db.execute(select(Role).where(Role.store_id == p.store.id).order_by(Role.builtin.desc(), Role.name)).scalars()
    return {"items": [role_out(r, counts.get(r.id, 0)) for r in roles]}


class RoleIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=300)
    permissions: list[str]


@router.post("/staff/roles")
def create_role(body: RoleIn, db: Session = Depends(get_db), p: Principal = Depends(require("staff.manage"))):
    try:
        perms = validate_permissions(body.permissions)
    except ValueError as exc:
        raise AppError("invalid_permissions", str(exc)) from exc
    if ALL in perms:
        raise Forbidden("Only the owner role can hold every permission")
    _no_escalation(p, perms)
    key = "custom_" + uuid.uuid4().hex[:8]
    r = Role(store_id=p.store.id, key=key, name=body.name, description=body.description, permissions=perms)
    db.add(r)
    db.flush()
    audit(db, p.store.id, p.actor, "staff.role_created", "role", r.id, {"permissions": perms})
    db.commit()
    return role_out(r)


def _no_escalation(p: Principal, perms: list[str]):
    if not p.can(ALL):
        extra = [x for x in perms if not p.can(x)]
        if extra:
            raise Forbidden(f"You cannot grant permissions you do not hold: {', '.join(extra)}")


@router.patch("/staff/roles/{role_id}")
def update_role(role_id: str, body: RoleIn, db: Session = Depends(get_db),
                p: Principal = Depends(require("staff.manage"))):
    r = db.get(Role, parse_uuid(role_id, "Role"))
    if r is None or r.store_id != p.store.id:
        raise NotFound("Role")
    if r.builtin:
        raise Forbidden("Built-in roles are managed by VOLTORA; duplicate it as a custom role instead")
    try:
        perms = validate_permissions(body.permissions)
    except ValueError as exc:
        raise AppError("invalid_permissions", str(exc)) from exc
    _no_escalation(p, perms)
    before = r.permissions
    r.name, r.description, r.permissions = body.name, body.description, perms
    audit(db, p.store.id, p.actor, "staff.role_updated", "role", r.id, {"from": before, "to": perms})
    db.commit()
    return role_out(r)


@router.delete("/staff/roles/{role_id}")
def delete_role(role_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("staff.manage"))):
    r = db.get(Role, parse_uuid(role_id, "Role"))
    if r is None or r.store_id != p.store.id:
        raise NotFound("Role")
    if r.builtin:
        raise Forbidden("Built-in roles cannot be deleted")
    if db.execute(select(Membership.id).where(Membership.role_id == r.id)).first():
        raise Conflict("role_in_use", "Move staff to another role before deleting this one")
    db.delete(r)
    audit(db, p.store.id, p.actor, "staff.role_deleted", "role", role_id)
    db.commit()
    return {"ok": True}


@router.get("/staff/members")
def list_members(db: Session = Depends(get_db), p: Principal = Depends(require("staff.read"))):
    rows = db.execute(select(Membership, User, Role).join(User, User.id == Membership.user_id)
                      .join(Role, Role.id == Membership.role_id).where(Membership.store_id == p.store.id)
                      .order_by(User.name)).all()
    return {"items": [{**user_out(u), "role": role_out(r), "expires_at": m.expires_at.isoformat() if m.expires_at else None,
                       "expired": bool(m.expires_at and m.expires_at <= utcnow())} for m, u, r in rows]}


class InviteIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=160)
    role_id: str
    expires_at: dt.datetime | None = None


@router.post("/staff/invite")
def invite(body: InviteIn, db: Session = Depends(get_db), p: Principal = Depends(require("staff.manage"))):
    from ..messaging.service import queue_message

    role = db.get(Role, parse_uuid(body.role_id, "Role"))
    if role is None or role.store_id != p.store.id:
        raise NotFound("Role")
    if role.key == "owner" and not p.can(ALL):
        raise Forbidden("Only an owner can invite another owner")
    _no_escalation(p, [x for x in role.permissions if x != ALL])
    u = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if u is None:
        u = create_user(db, email=body.email, password=None, name=body.name, kind="staff", status="invited")
    elif u.kind != "staff":
        raise Conflict("customer_account", "This email belongs to a customer account; use a work email")
    if db.execute(select(Membership.id).where(Membership.user_id == u.id, Membership.store_id == p.store.id)).first():
        raise Conflict("already_member", "This person already has access")
    db.add(Membership(user_id=u.id, store_id=p.store.id, role_id=role.id, expires_at=body.expires_at,
                      granted_by=p.user.id if p.user else None))
    raw = issue_token(db, u, "invite", 72)
    link = f"{get_settings().public_url}/admin/accept-invite?token={raw}"
    queue_message(db, p.store.id, channel="email", to=u.email, template="staff.invite",
                  context={"name": u.name, "store": p.store.name, "role": role.name, "link": link}, sensitive=True)
    emit(db, p.store.id, EV_STAFF_INVITED, {"user_id": str(u.id), "role": role.key})
    audit(db, p.store.id, p.actor, "staff.invited", "user", u.id, {"role": role.key,
                                                                    "expires_at": str(body.expires_at)})
    db.commit()
    # The link is returned to the inviter so access can be shared even before email delivery is configured.
    return {"user": user_out(u), "invite_link": link}


class AcceptInviteIn(BaseModel):
    token: str
    password: str = Field(max_length=200)
    name: str | None = None


@router.post("/auth/invite/accept")
def accept_invite(body: AcceptInviteIn, request: Request, response: Response, db: Session = Depends(get_db)):
    store = current_store(db)
    u = consume_token(db, body.token, "invite")
    problems = sec.password_problems(body.password)
    if problems:
        raise AppError("weak_password", problems[0])
    u.password_hash = sec.hash_password(body.password)
    u.status = "active"
    u.email_verified_at = utcnow()
    if body.name:
        u.name = body.name
    start_session(db, response, request, u, mfa_verified=not u.mfa_enabled_at)
    audit(db, store.id, Actor("user", u.id, u.email, client_ip(request)), "staff.invite_accepted", "user", u.id)
    db.commit()
    return {"user": user_out(u)}


class MemberPatch(BaseModel):
    role_id: str | None = None
    expires_at: dt.datetime | None = None
    clear_expiry: bool = False


@router.patch("/staff/members/{user_id}")
def update_member(user_id: str, body: MemberPatch, db: Session = Depends(get_db),
                  p: Principal = Depends(require("staff.manage"))):
    uid = parse_uuid(user_id, "Member")
    m = db.execute(select(Membership).where(Membership.user_id == uid, Membership.store_id == p.store.id)
                   ).scalar_one_or_none()
    if m is None:
        raise NotFound("Member")
    current_role = db.get(Role, m.role_id)
    if current_role.key == "owner" and not p.can(ALL):
        raise Forbidden("Only an owner can change another owner")
    if body.role_id:
        role = db.get(Role, parse_uuid(body.role_id, "Role"))
        if role is None or role.store_id != p.store.id:
            raise NotFound("Role")
        _no_escalation(p, [x for x in role.permissions if x != ALL])
        if current_role.key == "owner" and role.key != "owner":
            _guard_last_owner(db, p.store.id, uid)
        m.role_id = role.id
    if body.clear_expiry:
        m.expires_at = None
    elif body.expires_at:
        m.expires_at = body.expires_at
    audit(db, p.store.id, p.actor, "staff.member_updated", "user", uid, body.model_dump(mode="json"))
    db.commit()
    return {"ok": True}


def _guard_last_owner(db: Session, store_id, uid):
    owners = db.execute(select(func.count()).select_from(Membership).join(Role, Role.id == Membership.role_id)
                        .where(Membership.store_id == store_id, Role.key == "owner", Membership.user_id != uid)
                        ).scalar()
    if not owners:
        raise Conflict("last_owner", "A store must keep at least one owner")


@router.delete("/staff/members/{user_id}")
def remove_member(user_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("staff.manage"))):
    uid = parse_uuid(user_id, "Member")
    if p.user and uid == p.user.id:
        raise Conflict("self_removal", "You cannot remove your own access")
    m = db.execute(select(Membership).where(Membership.user_id == uid, Membership.store_id == p.store.id)
                   ).scalar_one_or_none()
    if m is None:
        raise NotFound("Member")
    if db.get(Role, m.role_id).key == "owner":
        if not p.can(ALL):
            raise Forbidden("Only an owner can remove an owner")
        _guard_last_owner(db, p.store.id, uid)
    db.delete(m)
    db.execute(update(AuthSession).where(AuthSession.user_id == uid).values(revoked_at=utcnow()))
    audit(db, p.store.id, p.actor, "staff.access_revoked", "user", uid)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ API keys
class ApiKeyIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    permissions: list[str]
    expires_at: dt.datetime | None = None


@router.get("/developer/api-keys")
def list_keys(db: Session = Depends(get_db), p: Principal = Depends(require("developer.manage"))):
    rows = db.execute(select(ApiKey).where(ApiKey.store_id == p.store.id).order_by(ApiKey.created_at.desc())).scalars()
    return {"items": [{"id": str(k.id), "name": k.name, "prefix": k.prefix, "permissions": k.permissions,
                       "created_at": k.created_at.isoformat(), "revoked": bool(k.revoked_at),
                       "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                       "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None} for k in rows]}


@router.post("/developer/api-keys")
def create_key(body: ApiKeyIn, db: Session = Depends(get_db), p: Principal = Depends(require("developer.manage"))):
    try:
        perms = validate_permissions(body.permissions)
    except ValueError as exc:
        raise AppError("invalid_permissions", str(exc)) from exc
    if ALL in perms:
        raise Forbidden("API keys must be scoped to explicit permissions")
    _no_escalation(p, perms)
    raw = "vk_" + sec.new_token(30)
    k = ApiKey(store_id=p.store.id, name=body.name, prefix=raw[:10], key_hash=sec.token_hash(raw), permissions=perms,
               created_by=p.user.id if p.user else None, expires_at=body.expires_at)
    db.add(k)
    db.flush()
    audit(db, p.store.id, p.actor, "developer.api_key_created", "api_key", k.id, {"permissions": perms})
    db.commit()
    return {"id": str(k.id), "key": raw, "note": "Copy this key now. It will not be shown again."}


@router.delete("/developer/api-keys/{key_id}")
def revoke_key(key_id: str, db: Session = Depends(get_db), p: Principal = Depends(require("developer.manage"))):
    k = db.get(ApiKey, parse_uuid(key_id, "API key"))
    if k is None or k.store_id != p.store.id:
        raise NotFound("API key")
    k.revoked_at = utcnow()
    audit(db, p.store.id, p.actor, "developer.api_key_revoked", "api_key", k.id)
    db.commit()
    return {"ok": True}
