"""Content/design operations. Every mutation is audited in the caller's transaction."""
from __future__ import annotations

import base64
import copy
import datetime as dt
import re
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.errors import AppError, Conflict, NotFound
from ...core.security import consteq, hmac_sha256
from ...core.services import NAV_COUNTERS, SEARCHERS, Actor, audit, emit, event_type, get_setting, setting
from ...db import utcnow
from ...seed import seed_hook
from . import starter_content
from .models import PAGE_KINDS, ContentPage, PageRevision, Theme, ThemeRevision
from .themes import DEFAULT_THEME_ID, STARTER_THEMES, publish_blockers, require_manifest
from .validation import Limits, diff_summary, iter_nodes, lint_document, require_valid

MAX_NODES = setting(key="content.max_nodes", type="int", default=5000, min=100, max=50_000, group="Website",
                    label="Maximum components per document",
                    description="Resource guard for very large pages. Raise it if a page legitimately needs more.")
MAX_BYTES = setting(key="content.max_document_bytes", type="int", default=1_000_000, min=50_000, max=10_000_000,
                    group="Website", label="Maximum page document size (bytes)")
MAX_CSS = setting(key="content.max_custom_css_bytes", type="int", default=20_000, min=1_000, max=200_000,
                  group="Website", label="Maximum custom CSS per component (bytes)")

EV_PAGE_PUBLISHED = event_type("content.page_published", "A storefront page or global section was published")
EV_PAGE_ROLLED_BACK = event_type("content.page_rolled_back", "A page was rolled back to an earlier revision")
EV_THEME_PUBLISHED = event_type("content.theme_published", "A theme revision was published or made live")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,158}$")
GLOBAL_SLUGS = ("header", "footer", "announcement")
PREVIEW_TTL = dt.timedelta(minutes=30)


def limits(db: Session, store_id: uuid.UUID) -> Limits:
    return Limits(max_nodes=get_setting(db, store_id, MAX_NODES.key),
                  max_bytes=get_setting(db, store_id, MAX_BYTES.key),
                  max_css_bytes=get_setting(db, store_id, MAX_CSS.key))


def empty_document() -> dict:
    return {"schemaVersion": 1, "root": {"id": "root", "type": "page", "props": {}, "children": []}}


# ------------------------------------------------------------------------------------------------ preview tokens
def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def make_preview_token(kind: str, obj_id: uuid.UUID, ttl: dt.timedelta = PREVIEW_TTL) -> tuple[str, dt.datetime]:
    exp = utcnow() + ttl
    body = f"{kind}:{obj_id}:{int(exp.timestamp())}"
    sig = hmac_sha256(get_settings().secret_key, "voltora-preview:" + body)[:40]
    return f"{_b64(body)}.{sig}", exp


def check_preview_token(token: str | None, kind: str, obj_id: uuid.UUID) -> bool:
    if not token or "." not in token:
        return False
    raw, sig = token.rsplit(".", 1)
    try:
        body = _unb64(raw)
        k, oid, exp = body.split(":")
    except (ValueError, UnicodeDecodeError):
        return False
    expected = hmac_sha256(get_settings().secret_key, "voltora-preview:" + body)[:40]
    if not consteq(sig, expected) or k != kind or oid != str(obj_id):
        return False
    return int(exp) > int(utcnow().timestamp())


# ------------------------------------------------------------------------------------------------ pages
def get_page(db: Session, store_id: uuid.UUID, page_id: uuid.UUID, lock: bool = False) -> ContentPage:
    q = select(ContentPage).where(ContentPage.id == page_id, ContentPage.store_id == store_id)
    if lock:
        q = q.with_for_update()
    page = db.execute(q).scalar_one_or_none()
    if page is None:
        raise NotFound("Page")
    return page


def _next_number(db: Session, page_id: uuid.UUID) -> int:
    n = db.execute(select(func.max(PageRevision.number)).where(PageRevision.page_id == page_id)).scalar()
    return (n or 0) + 1


def published_revision(db: Session, page: ContentPage) -> PageRevision | None:
    return db.get(PageRevision, page.published_revision_id) if page.published_revision_id else None


def page_summary(db: Session, page: ContentPage, pub: PageRevision | None = None) -> dict:
    pub = pub if pub is not None else published_revision(db, page)
    dirty = pub is None or pub.document != page.draft_document or pub.title != page.title or pub.seo != page.seo
    status = "archived" if page.archived_at else ("draft" if pub is None else ("changed" if dirty else "published"))
    return {"id": str(page.id), "kind": page.kind, "slug": page.slug, "title": page.title, "status": status,
            "needs_review": page.needs_review, "draft_version": page.draft_version,
            "draft_updated_at": page.draft_updated_at.isoformat(),
            "published_revision": pub.number if pub else None,
            "published_at": page.published_at.isoformat() if page.published_at else None,
            "path": public_path(page), "node_count": sum(1 for _ in iter_nodes(page.draft_document.get("root")))}


def public_path(page: ContentPage) -> str | None:
    if page.kind != "page":
        return None
    return "/" if page.slug == "home" else f"/pages/{page.slug}"


def page_detail(db: Session, page: ContentPage) -> dict:
    out = page_summary(db, page)
    out.update({"seo": page.seo, "document": page.draft_document, "meta": page.meta,
                "lint": lint_document(page.draft_document)})
    return out


def _clean_seo(seo: Any) -> dict:
    if seo is None:
        return {}
    if not isinstance(seo, dict):
        raise AppError("invalid_seo", "SEO must be an object", status=422)
    out = {}
    for k, lim in (("title", 120), ("description", 320), ("image", 500)):
        v = seo.get(k)
        if v is None:
            continue
        if not isinstance(v, str) or len(v) > lim:
            raise AppError("invalid_seo", f"SEO {k} must be text up to {lim} characters", status=422)
        out[k] = v.strip()
    if "noindex" in seo:
        out["noindex"] = bool(seo["noindex"])
    return out


def create_page(db: Session, store_id: uuid.UUID, actor: Actor, *, kind: str, slug: str, title: str,
                document: dict | None = None, seo: dict | None = None, needs_review: bool = False,
                meta: dict | None = None) -> tuple[ContentPage, list[dict]]:
    if kind not in PAGE_KINDS:
        raise AppError("invalid_kind", f"kind must be one of {', '.join(PAGE_KINDS)}", status=422)
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise AppError("invalid_slug", "Use lowercase letters, digits and dashes for the address", status=422)
    if not (title or "").strip() or len(title) > 200:
        raise AppError("invalid_title", "Title is required (up to 200 characters)", status=422)
    exists = db.execute(select(ContentPage.id).where(ContentPage.store_id == store_id, ContentPage.kind == kind,
                                                     ContentPage.slug == slug)).scalar()
    if exists:
        raise Conflict("slug_taken", f"A {kind} with the address {slug} already exists")
    doc, warnings = require_valid(document or empty_document(), limits(db, store_id))
    page = ContentPage(store_id=store_id, kind=kind, slug=slug, title=title.strip(), seo=_clean_seo(seo),
                       draft_document=doc, draft_version=1, draft_updated_by=actor.id, needs_review=needs_review,
                       meta=meta or {})
    db.add(page)
    db.flush()
    audit(db, store_id, actor, "content.page_created", "content_page", page.id, {"kind": kind, "slug": slug})
    return page, warnings


def save_draft(db: Session, store_id: uuid.UUID, actor: Actor, page_id: uuid.UUID, *, base_version: int,
               document: dict | None = None, title: str | None = None, seo: dict | None = None,
               slug: str | None = None, checkpoint: str | None = None) -> tuple[ContentPage, list[dict]]:
    page = get_page(db, store_id, page_id, lock=True)
    if page.draft_version != base_version:
        raise Conflict("draft_conflict", "Someone else saved this page since you opened it.",
                       details={"current_version": page.draft_version,
                                "updated_at": page.draft_updated_at.isoformat()})
    warnings: list[dict] = []
    if document is not None:
        page.draft_document, warnings = require_valid(document, limits(db, store_id))
    if title is not None:
        if not title.strip() or len(title) > 200:
            raise AppError("invalid_title", "Title is required (up to 200 characters)", status=422)
        page.title = title.strip()
    if seo is not None:
        page.seo = _clean_seo(seo)
    if slug is not None and slug != page.slug:
        if page.kind == "global" or page.slug == "home":
            raise AppError("slug_locked", "This page's address cannot change", status=422)
        if not SLUG_RE.match(slug):
            raise AppError("invalid_slug", "Use lowercase letters, digits and dashes for the address", status=422)
        if db.execute(select(ContentPage.id).where(ContentPage.store_id == store_id, ContentPage.kind == page.kind,
                                                   ContentPage.slug == slug)).scalar():
            raise Conflict("slug_taken", f"The address {slug} is already used")
        page.slug = slug
    page.draft_version += 1
    page.draft_updated_at = utcnow()
    page.draft_updated_by = actor.id
    if checkpoint is not None:
        _add_revision(db, page, actor, "checkpoint", checkpoint or "Checkpoint")
    return page, warnings


def _add_revision(db: Session, page: ContentPage, actor: Actor, kind: str, message: str,
                  source_number: int | None = None) -> PageRevision:
    pub = published_revision(db, page)
    rev = PageRevision(page_id=page.id, store_id=page.store_id, number=_next_number(db, page.id), kind=kind,
                       document=copy.deepcopy(page.draft_document), title=page.title, seo=dict(page.seo),
                       message=(message or "")[:500], source_number=source_number,
                       diff=diff_summary(pub.document if pub else None, page.draft_document),
                       author_id=actor.id if actor.kind == "user" else None, author_label=actor.label)
    db.add(rev)
    db.flush()
    return rev


def publish_page(db: Session, store_id: uuid.UUID, actor: Actor, page_id: uuid.UUID, *, message: str,
                 base_version: int | None = None) -> PageRevision:
    page = get_page(db, store_id, page_id, lock=True)
    if base_version is not None and base_version != page.draft_version:
        raise Conflict("draft_conflict", "The draft changed since you reviewed it. Review and publish again.",
                       details={"current_version": page.draft_version})
    if page.archived_at:
        raise AppError("archived", "Restore the page before publishing", status=422)
    page.draft_document, _ = require_valid(page.draft_document, limits(db, store_id))
    blocking = [x for x in lint_document(page.draft_document) if x["severity"] == "error"]
    if blocking:
        raise AppError("lint_failed", f"Fix {len(blocking)} accessibility problem(s) before publishing", status=422,
                       details={"lint": blocking})
    rev = _add_revision(db, page, actor, "publish", message or "Published")
    page.published_revision_id = rev.id
    page.published_at = utcnow()
    page.needs_review = False
    audit(db, store_id, actor, "content.page_published", "content_page", page.id,
          {"slug": page.slug, "kind": page.kind, "revision": rev.number, "diff": rev.diff.get("text")})
    emit(db, store_id, EV_PAGE_PUBLISHED, {"page_id": str(page.id), "slug": page.slug, "kind": page.kind,
                                           "revision": rev.number})
    return rev


def get_revision(db: Session, page: ContentPage, number: int) -> PageRevision:
    rev = db.execute(select(PageRevision).where(PageRevision.page_id == page.id, PageRevision.number == number)
                     ).scalar_one_or_none()
    if rev is None:
        raise NotFound("Revision")
    return rev


def rollback_page(db: Session, store_id: uuid.UUID, actor: Actor, page_id: uuid.UUID, number: int,
                  message: str = "") -> PageRevision:
    """Publish a copy of an earlier revision as a new revision; the draft follows so editing continues from it."""
    page = get_page(db, store_id, page_id, lock=True)
    old = get_revision(db, page, number)
    page.draft_document = copy.deepcopy(old.document)
    page.title, page.seo = old.title, dict(old.seo)
    page.draft_version += 1
    page.draft_updated_at = utcnow()
    rev = _add_revision(db, page, actor, "rollback", message or f"Rolled back to revision {number}", number)
    page.published_revision_id = rev.id
    page.published_at = utcnow()
    audit(db, store_id, actor, "content.page_rolled_back", "content_page", page.id,
          {"slug": page.slug, "to": number, "revision": rev.number})
    emit(db, store_id, EV_PAGE_ROLLED_BACK, {"page_id": str(page.id), "slug": page.slug, "revision": rev.number,
                                             "source": number})
    return rev


def restore_to_draft(db: Session, store_id: uuid.UUID, actor: Actor, page_id: uuid.UUID, number: int,
                     base_version: int) -> ContentPage:
    page = get_page(db, store_id, page_id, lock=True)
    if page.draft_version != base_version:
        raise Conflict("draft_conflict", "Someone else saved this page since you opened it.",
                       details={"current_version": page.draft_version})
    old = get_revision(db, page, number)
    page.draft_document = copy.deepcopy(old.document)
    page.title, page.seo = old.title, dict(old.seo)
    page.draft_version += 1
    page.draft_updated_at = utcnow()
    page.draft_updated_by = actor.id
    audit(db, store_id, actor, "content.page_restored_to_draft", "content_page", page.id, {"from": number})
    return page


def export_page(page: ContentPage) -> dict:
    return {"format": "voltora.page", "formatVersion": 1, "kind": page.kind, "slug": page.slug,
            "title": page.title, "seo": page.seo, "document": page.draft_document,
            "exported_at": utcnow().isoformat()}


def parse_page_export(data: Any) -> dict:
    if not isinstance(data, dict) or data.get("format") != "voltora.page":
        raise AppError("invalid_import", "This file is not a VOLTORA page export", status=422)
    if data.get("formatVersion") != 1:
        raise AppError("invalid_import", f"Unsupported export version {data.get('formatVersion')}", status=422)
    return data


# ------------------------------------------------------------------------------------------------ public rendering
def _active(v: dict, now: dt.datetime) -> bool:
    from .validation import _iso

    if v.get("starts_at") and _iso(v["starts_at"]) > now:
        return False
    return not (v.get("ends_at") and _iso(v["ends_at"]) <= now)


def prune_for_visitor(node: dict | None, now: dt.datetime, tags: set[str] | None) -> dict | None:
    """Server-side rules: hidden flag, schedule window and customer tags (client evaluates device/auth/locale)."""
    if node is None:
        return None
    v = node.get("visibility") or {}
    if v.get("hidden") or not _active(v, now):
        return None
    if v.get("customer_tags") and not (tags and set(v["customer_tags"]) & tags):
        return None
    out = {k: val for k, val in node.items() if k != "children"}
    if out.get("visibility"):
        out["visibility"] = {k: val for k, val in out["visibility"].items() if k not in ("customer_tags",)}
    kids = [c for c in (prune_for_visitor(k, now, tags) for k in node.get("children") or []) if c]
    if kids:
        out["children"] = kids
    return out


def visitor_tags(db: Session, p) -> set[str]:
    if p is None or p.user is None:
        return set()
    from ..customers.models import Customer

    tags = db.execute(select(Customer.tags).where(Customer.store_id == p.store.id, Customer.user_id == p.user.id)
                      ).scalar()
    return {str(t) for t in (tags or [])}


def published_doc(db: Session, store_id: uuid.UUID, kind: str, slug: str) -> tuple[ContentPage, PageRevision] | None:
    row = db.execute(select(ContentPage, PageRevision).join(PageRevision,
                                                            PageRevision.id == ContentPage.published_revision_id)
                     .where(ContentPage.store_id == store_id, ContentPage.kind == kind, ContentPage.slug == slug,
                            ContentPage.archived_at.is_(None))).first()
    return (row[0], row[1]) if row else None


def published_page_list(db: Session, store_id: uuid.UUID) -> list[dict]:
    rows = db.execute(select(ContentPage.slug, ContentPage.title).where(
        ContentPage.store_id == store_id, ContentPage.kind == "page", ContentPage.archived_at.is_(None),
        ContentPage.published_revision_id.is_not(None)).order_by(ContentPage.slug)).all()
    return [{"slug": s, "title": t} for s, t in rows]


# ------------------------------------------------------------------------------------------------ themes
def get_theme(db: Session, store_id: uuid.UUID, theme_id: uuid.UUID, lock: bool = False) -> Theme:
    q = select(Theme).where(Theme.id == theme_id, Theme.store_id == store_id)
    if lock:
        q = q.with_for_update()
    t = db.execute(q).scalar_one_or_none()
    if t is None:
        raise NotFound("Theme")
    return t


def _theme_next(db: Session, theme_id: uuid.UUID) -> int:
    n = db.execute(select(func.max(ThemeRevision.number)).where(ThemeRevision.theme_id == theme_id)).scalar()
    return (n or 0) + 1


def theme_summary(db: Session, t: Theme) -> dict:
    pub = db.get(ThemeRevision, t.published_revision_id) if t.published_revision_id else None
    return {"id": str(t.id), "key": t.key, "name": t.name, "family": t.family, "is_live": t.is_live,
            "origin": t.origin, "forked_from": t.forked_from, "draft_version": t.draft_version,
            "version": t.draft_manifest.get("version"), "description": t.draft_manifest.get("description", ""),
            "published_revision": pub.number if pub else None,
            "status": "draft" if pub is None else ("changed" if pub.manifest != t.draft_manifest else "published"),
            "draft_updated_at": t.draft_updated_at.isoformat(),
            "swatch": {k: t.draft_manifest.get("tokens", {}).get("color", {}).get(k)
                       for k in ("bg", "surface", "text", "primary", "accent")},
            "components": t.draft_manifest.get("components", {})}


def _unique_key(db: Session, store_id: uuid.UUID, base: str) -> str:
    base = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")[:50] or "theme"
    key, i = base, 2
    while db.execute(select(Theme.id).where(Theme.store_id == store_id, Theme.key == key)).scalar():
        key = f"{base}-{i}"
        i += 1
    return key


def create_theme(db: Session, store_id: uuid.UUID, actor: Actor, manifest: dict, origin: str,
                 forked_from: str | None = None, key: str | None = None) -> tuple[Theme, list[dict]]:
    clean, warnings = require_manifest(manifest)
    key = key or _unique_key(db, store_id, clean.get("id") or clean["name"])
    clean["id"] = key
    t = Theme(store_id=store_id, key=key, name=clean["name"], family=clean.get("family") or "", origin=origin,
              draft_manifest=clean, forked_from=forked_from)
    db.add(t)
    db.flush()
    audit(db, store_id, actor, "content.theme_created", "theme", t.id, {"key": key, "origin": origin})
    return t, warnings


def save_theme_draft(db: Session, store_id: uuid.UUID, actor: Actor, theme_id: uuid.UUID, manifest: dict,
                     base_version: int) -> tuple[Theme, list[dict]]:
    t = get_theme(db, store_id, theme_id, lock=True)
    if t.draft_version != base_version:
        raise Conflict("draft_conflict", "This theme changed since you opened it.",
                       details={"current_version": t.draft_version})
    clean, warnings = require_manifest(manifest)
    clean["id"] = t.key
    t.draft_manifest = clean
    t.name, t.family = clean["name"], clean.get("family") or t.family
    t.draft_version += 1
    t.draft_updated_at = utcnow()
    return t, warnings


def publish_theme(db: Session, store_id: uuid.UUID, actor: Actor, theme_id: uuid.UUID, message: str,
                  base_version: int | None = None, make_live: bool = False, kind: str = "publish",
                  source: int | None = None) -> ThemeRevision:
    t = get_theme(db, store_id, theme_id, lock=True)
    if base_version is not None and base_version != t.draft_version:
        raise Conflict("draft_conflict", "This theme changed since you reviewed it.",
                       details={"current_version": t.draft_version})
    clean, _ = require_manifest(t.draft_manifest)
    blockers = publish_blockers(clean)
    if blockers:
        raise AppError("contrast_failed", f"{len(blockers)} colour pair(s) are below 4.5:1 for body text",
                       status=422, details={"contrast": blockers})
    rev = ThemeRevision(theme_id=t.id, store_id=store_id, number=_theme_next(db, t.id), kind=kind, manifest=clean,
                        message=(message or "Published")[:500], source_number=source,
                        author_id=actor.id if actor.kind == "user" else None, author_label=actor.label)
    db.add(rev)
    db.flush()
    t.published_revision_id = rev.id
    if make_live:
        _make_live(db, store_id, t)
    audit(db, store_id, actor, f"content.theme_{kind}", "theme", t.id,
          {"key": t.key, "revision": rev.number, "live": t.is_live})
    emit(db, store_id, EV_THEME_PUBLISHED, {"theme_id": str(t.id), "key": t.key, "revision": rev.number,
                                            "live": t.is_live})
    return rev


def _make_live(db: Session, store_id: uuid.UUID, t: Theme) -> None:
    db.execute(update(Theme).where(Theme.store_id == store_id, Theme.id != t.id, Theme.is_live.is_(True))
               .values(is_live=False))
    db.flush()
    t.is_live = True


def activate_theme(db: Session, store_id: uuid.UUID, actor: Actor, theme_id: uuid.UUID) -> Theme:
    t = get_theme(db, store_id, theme_id, lock=True)
    if not t.published_revision_id:
        raise AppError("not_published", "Publish this theme before making it live", status=422)
    _make_live(db, store_id, t)
    audit(db, store_id, actor, "content.theme_activated", "theme", t.id, {"key": t.key})
    emit(db, store_id, EV_THEME_PUBLISHED, {"theme_id": str(t.id), "key": t.key, "live": True})
    return t


def rollback_theme(db: Session, store_id: uuid.UUID, actor: Actor, theme_id: uuid.UUID, number: int,
                   message: str = "") -> ThemeRevision:
    t = get_theme(db, store_id, theme_id, lock=True)
    old = db.execute(select(ThemeRevision).where(ThemeRevision.theme_id == t.id, ThemeRevision.number == number)
                     ).scalar_one_or_none()
    if old is None:
        raise NotFound("Theme revision")
    t.draft_manifest = copy.deepcopy(old.manifest)
    t.name = old.manifest.get("name", t.name)
    t.draft_version += 1
    t.draft_updated_at = utcnow()
    db.flush()
    return publish_theme(db, store_id, actor, t.id, message or f"Rolled back to revision {number}", kind="rollback",
                         source=number)


def live_theme(db: Session, store_id: uuid.UUID) -> tuple[Theme, ThemeRevision] | None:
    row = db.execute(select(Theme, ThemeRevision).join(ThemeRevision, ThemeRevision.id == Theme.published_revision_id)
                     .where(Theme.store_id == store_id, Theme.is_live.is_(True))).first()
    return (row[0], row[1]) if row else None


def export_theme(t: Theme) -> dict:
    return {"format": "voltora.theme", "formatVersion": 1, "manifest": t.draft_manifest,
            "exported_at": utcnow().isoformat()}


# ------------------------------------------------------------------------------------------------ seed
@seed_hook(60, "content")
def seed_content(db: Session, store, demo: bool) -> dict:
    """Idempotent: installs starter themes and documents that do not exist yet; never overwrites edits."""
    from ...core.services import SYSTEM

    created: dict[str, list[str]] = {"themes": [], "pages": []}
    have = set(db.execute(select(Theme.key).where(Theme.store_id == store.id)).scalars())
    for m in STARTER_THEMES:
        if m["id"] in have:
            continue
        t, _ = create_theme(db, store.id, SYSTEM, copy.deepcopy(m), origin="starter", key=m["id"])
        created["themes"].append(t.key)
        if m["id"] == DEFAULT_THEME_ID:
            publish_theme(db, store.id, SYSTEM, t.id, "Installed starter theme", kind="seed")
    if not db.execute(select(Theme.id).where(Theme.store_id == store.id, Theme.is_live.is_(True))).scalar():
        default = db.execute(select(Theme).where(Theme.store_id == store.id, Theme.key == DEFAULT_THEME_ID)
                             ).scalar_one_or_none()
        if default is not None and default.published_revision_id:
            _make_live(db, store.id, default)

    existing = {(k, s) for k, s in db.execute(select(ContentPage.kind, ContentPage.slug)
                                              .where(ContentPage.store_id == store.id)).all()}
    for spec in starter_content.documents():
        if (spec["kind"], spec["slug"]) in existing:
            continue
        page, _ = create_page(db, store.id, SYSTEM, kind=spec["kind"], slug=spec["slug"], title=spec["title"],
                              document=spec["document"], seo=spec.get("seo"),
                              needs_review=spec.get("needs_review", False), meta=spec.get("meta"))
        created["pages"].append(f"{spec['kind']}:{spec['slug']}")
        if spec.get("publish"):
            publish_page(db, store.id, SYSTEM, page.id, message="Installed starter content")
    db.flush()
    return created


# ------------------------------------------------------------------------------------------------ admin shell hooks
def _nav_counts(db: Session, p) -> dict:
    if not p.can("content.read"):
        return {}
    n = db.execute(select(func.count()).select_from(ContentPage).where(
        ContentPage.store_id == p.store.id, ContentPage.needs_review.is_(True),
        ContentPage.archived_at.is_(None))).scalar() or 0
    return {"design": n} if n else {}


def _search(db: Session, p, q: str) -> list[dict]:
    if not p.can("content.read") or len(q) < 2:
        return []
    like = f"%{q.lower()}%"
    rows = db.execute(select(ContentPage).where(
        ContentPage.store_id == p.store.id, ContentPage.archived_at.is_(None),
        func.lower(ContentPage.title).like(like) | func.lower(ContentPage.slug).like(like)).limit(6)).scalars()
    return [{"label": r.title, "sublabel": f"{r.kind} · /{r.slug}", "path": f"/admin/design/studio/{r.id}",
             "kind": "Website"} for r in rows]


NAV_COUNTERS.append(_nav_counts)
SEARCHERS.append(_search)
