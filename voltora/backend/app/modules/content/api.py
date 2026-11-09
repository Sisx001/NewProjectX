"""Design/content API: component registry, pages & revisions, themes, previews, public storefront reads."""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, principal, require, resolve_principal
from ...core.errors import AppError, Conflict, Forbidden, Unauthorized
from ...core.services import audit
from ...db import get_db, utcnow
from . import service as svc
from .models import ContentPage, PageRevision, Theme, ThemeRevision
from .registry import PROP_TYPES, STYLE_GROUPS, catalog
from .themes import (
    COLOR_KEYS,
    CONTRAST_PAIRS,
    DEFAULT_THEME_ID,
    FONTS,
    VARIANTS,
    contrast_report,
    require_manifest,
    starter,
)
from .validation import lint_document, sanitize_css, scope_css, validate_document

MODULE = {"title": "Website", "description": "Pages, global sections, themes, revisions and the visual studio"}
router = APIRouter(prefix="/api", tags=["content"])

READ = require("content.read")
WRITE = require("content.write")
PUBLISH = require("content.publish")


# ------------------------------------------------------------------------------------------------ bodies
class DocBody(BaseModel):
    document: dict[str, Any]


class CssBody(BaseModel):
    css: str = Field(max_length=500_000)
    node_id: str = Field(default="node", max_length=64)


class PageCreate(BaseModel):
    kind: str = "page"
    slug: str = Field(max_length=160)
    title: str = Field(max_length=200)
    document: dict[str, Any] | None = None
    seo: dict[str, Any] | None = None
    copy_from: str | None = None  # page id to start from (template/symbol/page)


class DraftBody(BaseModel):
    base_version: int
    document: dict[str, Any] | None = None
    title: str | None = Field(default=None, max_length=200)
    seo: dict[str, Any] | None = None
    slug: str | None = Field(default=None, max_length=160)
    checkpoint: str | None = Field(default=None, max_length=500)


class PublishBody(BaseModel):
    message: str = Field(default="", max_length=500)
    base_version: int | None = None


class RevBody(BaseModel):
    number: int
    message: str = Field(default="", max_length=500)
    base_version: int | None = None


class ImportBody(BaseModel):
    data: dict[str, Any]
    page_id: str | None = None
    base_version: int | None = None


class ThemeDraft(BaseModel):
    base_version: int
    manifest: dict[str, Any]


class ThemePublish(BaseModel):
    message: str = Field(default="", max_length=500)
    base_version: int | None = None
    make_live: bool = False


class ForkBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ThemeImport(BaseModel):
    data: dict[str, Any]


# ------------------------------------------------------------------------------------------------ registry
@router.get("/content/components")
def components(db: Session = Depends(get_db), p: Principal = Depends(READ)):
    lim = svc.limits(db, p.store.id)
    return {"components": catalog(), "prop_types": sorted(PROP_TYPES), "style_groups": STYLE_GROUPS,
            "fonts": [{"key": k, **v} for k, v in FONTS.items()], "theme_variants": VARIANTS,
            "color_keys": COLOR_KEYS, "contrast_pairs": [{"fg": a, "bg": b, "kind": c} for a, b, c in CONTRAST_PAIRS],
            "limits": {"max_nodes": lim.max_nodes, "max_bytes": lim.max_bytes, "max_css_bytes": lim.max_css_bytes}}


@router.post("/content/validate")
def validate(body: DocBody, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    clean, rep = validate_document(body.document, svc.limits(db, p.store.id))
    return {"ok": not rep.errors, "errors": rep.errors[:500], "warnings": rep.warnings[:500],
            "lint": lint_document(clean) if not rep.errors else []}


@router.post("/content/css/sanitize")
def css_sanitize(body: CssBody, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    css, warnings = sanitize_css(body.css, svc.limits(db, p.store.id).max_css_bytes)
    return {"css": css, "scoped": scope_css(css, body.node_id), "warnings": warnings}


# ------------------------------------------------------------------------------------------------ pages
@router.get("/content/pages")
def list_pages(kind: str | None = None, q: str | None = None, include_archived: bool = False,
               db: Session = Depends(get_db), p: Principal = Depends(READ)):
    stmt = select(ContentPage).where(ContentPage.store_id == p.store.id)
    if kind:
        stmt = stmt.where(ContentPage.kind == kind)
    if not include_archived:
        stmt = stmt.where(ContentPage.archived_at.is_(None))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(ContentPage.title.ilike(like) | ContentPage.slug.ilike(like))
    rows = db.execute(stmt.order_by(ContentPage.kind, ContentPage.slug)).scalars().all()
    pubs = {r.id: r for r in db.execute(select(PageRevision).where(
        PageRevision.id.in_([x.published_revision_id for x in rows if x.published_revision_id]))).scalars()}
    return {"items": [svc.page_summary(db, r, pubs.get(r.published_revision_id)) if r.published_revision_id
                      else svc.page_summary(db, r) for r in rows]}


@router.post("/content/pages")
def create_page(body: PageCreate, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    document = body.document
    if body.copy_from:
        src = svc.get_page(db, p.store.id, parse_uuid(body.copy_from, "Page"))
        document = src.draft_document
    page, warnings = svc.create_page(db, p.store.id, p.actor, kind=body.kind, slug=body.slug, title=body.title,
                                     document=document, seo=body.seo)
    db.commit()
    return {**svc.page_detail(db, page), "warnings": warnings}


@router.get("/content/pages/{page_id}")
def get_page(page_id: str, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    return svc.page_detail(db, svc.get_page(db, p.store.id, parse_uuid(page_id, "Page")))


@router.put("/content/pages/{page_id}/draft")
def save_draft(page_id: str, body: DraftBody, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    page, warnings = svc.save_draft(db, p.store.id, p.actor, parse_uuid(page_id, "Page"),
                                    base_version=body.base_version, document=body.document, title=body.title,
                                    seo=body.seo, slug=body.slug, checkpoint=body.checkpoint)
    if body.checkpoint is not None or body.slug is not None:
        audit(db, p.store.id, p.actor, "content.page_draft_saved", "content_page", page.id,
              {"version": page.draft_version, "checkpoint": body.checkpoint})
    db.commit()
    return {**svc.page_detail(db, page), "warnings": warnings}


@router.post("/content/pages/{page_id}/publish")
def publish(page_id: str, body: PublishBody, db: Session = Depends(get_db), p: Principal = Depends(PUBLISH)):
    rev = svc.publish_page(db, p.store.id, p.actor, parse_uuid(page_id, "Page"), message=body.message,
                           base_version=body.base_version)
    db.commit()
    page = svc.get_page(db, p.store.id, rev.page_id)
    return {"revision": _rev(rev), "page": svc.page_summary(db, page)}


def _rev(r: PageRevision, full: bool = False) -> dict:
    out = {"number": r.number, "kind": r.kind, "message": r.message, "title": r.title,
           "source_number": r.source_number, "diff": r.diff, "author": r.author_label or "System",
           "created_at": r.created_at.isoformat()}
    if full:
        out.update({"document": r.document, "seo": r.seo})
    return out


@router.get("/content/pages/{page_id}/revisions")
def revisions(page_id: str, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    page = svc.get_page(db, p.store.id, parse_uuid(page_id, "Page"))
    pub = svc.published_revision(db, page)
    rows = db.execute(select(PageRevision).where(PageRevision.page_id == page.id)
                      .order_by(PageRevision.number.desc()).limit(500)).scalars()
    return {"items": [{**_rev(r), "live": pub is not None and r.id == pub.id} for r in rows]}


@router.get("/content/revisions")
def recent_revisions(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db),
                     p: Principal = Depends(READ)):
    rows = db.execute(select(PageRevision, ContentPage).join(ContentPage, ContentPage.id == PageRevision.page_id)
                      .where(PageRevision.store_id == p.store.id)
                      .order_by(PageRevision.created_at.desc()).limit(limit)).all()
    return {"items": [{**_rev(r), "page_id": str(pg.id), "page_title": pg.title, "kind": pg.kind, "slug": pg.slug,
                       "revision_kind": r.kind, "live": pg.published_revision_id == r.id} for r, pg in rows]}


@router.get("/content/pages/{page_id}/revisions/{number}")
def revision(page_id: str, number: int, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    page = svc.get_page(db, p.store.id, parse_uuid(page_id, "Page"))
    return _rev(svc.get_revision(db, page, number), full=True)


@router.get("/content/pages/{page_id}/diff")
def diff(page_id: str, from_: int | None = Query(default=None, alias="from"), to: int | None = None,
         db: Session = Depends(get_db), p: Principal = Depends(READ)):
    page = svc.get_page(db, p.store.id, parse_uuid(page_id, "Page"))
    if from_ is None:
        pub = svc.published_revision(db, page)
        a = pub.document if pub else None
    else:
        a = svc.get_revision(db, page, from_).document
    b = svc.get_revision(db, page, to).document if to is not None else page.draft_document
    return svc.diff_summary(a, b)


@router.post("/content/pages/{page_id}/rollback")
def rollback(page_id: str, body: RevBody, db: Session = Depends(get_db), p: Principal = Depends(PUBLISH)):
    rev = svc.rollback_page(db, p.store.id, p.actor, parse_uuid(page_id, "Page"), body.number, body.message)
    db.commit()
    return {"revision": _rev(rev), "page": svc.page_detail(db, svc.get_page(db, p.store.id, rev.page_id))}


@router.post("/content/pages/{page_id}/restore")
def restore(page_id: str, body: RevBody, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    if body.base_version is None:
        raise AppError("base_version_required", "base_version is required", status=422)
    page = svc.restore_to_draft(db, p.store.id, p.actor, parse_uuid(page_id, "Page"), body.number,
                                body.base_version)
    db.commit()
    return svc.page_detail(db, page)


@router.delete("/content/pages/{page_id}")
def archive(page_id: str, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    page = svc.get_page(db, p.store.id, parse_uuid(page_id, "Page"), lock=True)
    if page.kind == "global" or (page.kind == "page" and page.slug == "home"):
        raise AppError("protected_page", "The home page and global sections cannot be archived", status=422)
    if page.published_revision_id and not p.can("content.publish"):
        raise Forbidden("Archiving a live page takes it off the storefront and needs content.publish")
    page.archived_at = utcnow()
    audit(db, p.store.id, p.actor, "content.page_archived", "content_page", page.id, {"slug": page.slug})
    db.commit()
    return {"ok": True}


@router.post("/content/pages/{page_id}/unarchive")
def unarchive(page_id: str, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    page = svc.get_page(db, p.store.id, parse_uuid(page_id, "Page"), lock=True)
    page.archived_at = None
    audit(db, p.store.id, p.actor, "content.page_unarchived", "content_page", page.id, {"slug": page.slug})
    db.commit()
    return svc.page_summary(db, page)


@router.get("/content/pages/{page_id}/export")
def export(page_id: str, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    return svc.export_page(svc.get_page(db, p.store.id, parse_uuid(page_id, "Page")))


@router.post("/content/pages/import")
def import_page(body: ImportBody, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    data = svc.parse_page_export(body.data)
    if body.page_id:
        if body.base_version is None:
            raise AppError("base_version_required", "base_version is required to replace a draft", status=422)
        page, warnings = svc.save_draft(db, p.store.id, p.actor, parse_uuid(body.page_id, "Page"),
                                        base_version=body.base_version, document=data.get("document"),
                                        title=data.get("title"), seo=data.get("seo"),
                                        checkpoint="Imported from file")
    else:
        page, warnings = svc.create_page(db, p.store.id, p.actor, kind=data.get("kind", "page"),
                                         slug=str(data.get("slug", "")), title=str(data.get("title", "")),
                                         document=data.get("document"), seo=data.get("seo"))
    audit(db, p.store.id, p.actor, "content.page_imported", "content_page", page.id, {"slug": page.slug})
    db.commit()
    return {**svc.page_detail(db, page), "warnings": warnings}


@router.post("/content/pages/{page_id}/preview-token")
def page_preview_token(page_id: str, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    page = svc.get_page(db, p.store.id, parse_uuid(page_id, "Page"))
    token, exp = svc.make_preview_token("page", page.id)
    return {"token": token, "expires_at": exp.isoformat(), "url": f"/__preview/{page.id}?token={token}"}


def _preview_access(request_p: Principal, token: str | None, kind: str, obj_id) -> None:
    if svc.check_preview_token(token, kind, obj_id):
        return
    if token:
        raise Unauthorized("This preview link has expired or is invalid")
    if request_p.user is None and request_p.api_key is None:
        raise Unauthorized()
    if not request_p.is_staff or not request_p.can("content.read"):
        raise Forbidden("Previewing drafts needs content.read or a preview link")


@router.get("/content/preview/pages/{page_id}")
def preview_page(page_id: str, token: str | None = None, db: Session = Depends(get_db),
                 p: Principal = Depends(principal)):
    pid = parse_uuid(page_id, "Page")
    _preview_access(p, token, "page", pid)
    page = svc.get_page(db, p.store.id, pid)
    return {"id": str(page.id), "kind": page.kind, "slug": page.slug, "title": page.title, "seo": page.seo,
            "document": page.draft_document, "draft_version": page.draft_version, "preview": True}


# ------------------------------------------------------------------------------------------------ themes
@router.get("/content/themes")
def list_themes(db: Session = Depends(get_db), p: Principal = Depends(READ)):
    rows = db.execute(select(Theme).where(Theme.store_id == p.store.id).order_by(Theme.is_live.desc(), Theme.name)
                      ).scalars()
    return {"items": [svc.theme_summary(db, t) for t in rows]}


def _theme_detail(db: Session, t: Theme) -> dict:
    return {**svc.theme_summary(db, t), "manifest": t.draft_manifest,
            "contrast": contrast_report(t.draft_manifest.get("tokens", {}).get("color", {}))}


@router.get("/content/themes/{theme_id}")
def get_theme(theme_id: str, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    return _theme_detail(db, svc.get_theme(db, p.store.id, parse_uuid(theme_id, "Theme")))


@router.put("/content/themes/{theme_id}/draft")
def save_theme(theme_id: str, body: ThemeDraft, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    t, warnings = svc.save_theme_draft(db, p.store.id, p.actor, parse_uuid(theme_id, "Theme"), body.manifest,
                                       body.base_version)
    db.commit()
    return {**_theme_detail(db, t), "warnings": warnings}


@router.post("/content/themes/{theme_id}/publish")
def publish_theme(theme_id: str, body: ThemePublish, db: Session = Depends(get_db),
                  p: Principal = Depends(PUBLISH)):
    rev = svc.publish_theme(db, p.store.id, p.actor, parse_uuid(theme_id, "Theme"), body.message,
                            base_version=body.base_version, make_live=body.make_live)
    db.commit()
    return {"revision": _trev(rev), "theme": _theme_detail(db, svc.get_theme(db, p.store.id, rev.theme_id))}


@router.post("/content/themes/{theme_id}/activate")
def activate_theme(theme_id: str, db: Session = Depends(get_db), p: Principal = Depends(PUBLISH)):
    t = svc.activate_theme(db, p.store.id, p.actor, parse_uuid(theme_id, "Theme"))
    db.commit()
    return _theme_detail(db, t)


@router.post("/content/themes/{theme_id}/fork")
def fork_theme(theme_id: str, body: ForkBody, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    src = svc.get_theme(db, p.store.id, parse_uuid(theme_id, "Theme"))
    manifest = {**src.draft_manifest, "name": body.name.strip(), "id": "", "version": "1.0.0"}
    t, _ = svc.create_theme(db, p.store.id, p.actor, manifest, origin="fork", forked_from=src.key)
    db.commit()
    return _theme_detail(db, t)


def _trev(r: ThemeRevision, full: bool = False) -> dict:
    out = {"number": r.number, "kind": r.kind, "message": r.message, "source_number": r.source_number,
           "author": r.author_label or "System", "created_at": r.created_at.isoformat(),
           "version": r.manifest.get("version")}
    if full:
        out["manifest"] = r.manifest
    return out


@router.get("/content/themes/{theme_id}/revisions")
def theme_revisions(theme_id: str, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    t = svc.get_theme(db, p.store.id, parse_uuid(theme_id, "Theme"))
    rows = db.execute(select(ThemeRevision).where(ThemeRevision.theme_id == t.id)
                      .order_by(ThemeRevision.number.desc()).limit(500)).scalars()
    return {"items": [{**_trev(r), "live": r.id == t.published_revision_id} for r in rows]}


@router.post("/content/themes/{theme_id}/rollback")
def rollback_theme(theme_id: str, body: RevBody, db: Session = Depends(get_db), p: Principal = Depends(PUBLISH)):
    rev = svc.rollback_theme(db, p.store.id, p.actor, parse_uuid(theme_id, "Theme"), body.number, body.message)
    db.commit()
    return {"revision": _trev(rev), "theme": _theme_detail(db, svc.get_theme(db, p.store.id, rev.theme_id))}


@router.get("/content/themes/{theme_id}/export")
def export_theme(theme_id: str, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    return svc.export_theme(svc.get_theme(db, p.store.id, parse_uuid(theme_id, "Theme")))


@router.post("/content/themes/import")
def import_theme(body: ThemeImport, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    data = body.data
    if data.get("format") != "voltora.theme" or data.get("formatVersion") != 1:
        raise AppError("invalid_import", "This file is not a VOLTORA theme export", status=422)
    manifest = dict(data.get("manifest") or {})
    manifest["id"] = ""  # keys are assigned per store so imports never overwrite an existing theme
    t, warnings = svc.create_theme(db, p.store.id, p.actor, manifest, origin="import")
    db.commit()
    return {**_theme_detail(db, t), "warnings": warnings}


@router.delete("/content/themes/{theme_id}")
def delete_theme(theme_id: str, db: Session = Depends(get_db), p: Principal = Depends(WRITE)):
    t = svc.get_theme(db, p.store.id, parse_uuid(theme_id, "Theme"), lock=True)
    if t.is_live:
        raise Conflict("theme_live", "Make another theme live before deleting this one")
    audit(db, p.store.id, p.actor, "content.theme_deleted", "theme", t.id, {"key": t.key})
    t.published_revision_id = None
    db.flush()
    db.delete(t)
    db.commit()
    return {"ok": True}


@router.post("/content/themes/{theme_id}/preview-token")
def theme_preview_token(theme_id: str, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    t = svc.get_theme(db, p.store.id, parse_uuid(theme_id, "Theme"))
    token, exp = svc.make_preview_token("theme", t.id)
    return {"token": token, "expires_at": exp.isoformat()}


@router.get("/content/preview/themes/{theme_id}")
def preview_theme(theme_id: str, token: str | None = None, db: Session = Depends(get_db),
                  p: Principal = Depends(principal)):
    tid = parse_uuid(theme_id, "Theme")
    _preview_access(p, token, "theme", tid)
    t = svc.get_theme(db, p.store.id, tid)
    return _public_theme(db, p, t.draft_manifest, t, None)


# ------------------------------------------------------------------------------------------------ public
def _globals(db: Session, p: Principal) -> dict:
    now = dt.datetime.now(dt.UTC)
    tags = svc.visitor_tags(db, p)
    out: dict[str, Any] = {}
    for slug in svc.GLOBAL_SLUGS:
        found = svc.published_doc(db, p.store.id, "global", slug)
        out[slug] = svc.prune_for_visitor(found[1].document.get("root"), now, tags) if found else None
    return out


def _public_theme(db: Session, p: Principal, manifest: dict, t: Theme | None, rev: ThemeRevision | None) -> dict:
    return {"id": manifest.get("id"), "name": manifest.get("name"), "version": manifest.get("version"),
            "family": manifest.get("family"), "tokens": manifest.get("tokens", {}),
            "components": manifest.get("components", {}), "revision": rev.number if rev else None,
            "fonts": {k: v["stack"] for k, v in FONTS.items()}, "globals": _globals(db, p),
            "pages": svc.published_page_list(db, p.store.id), "fallback": t is None}


@router.get("/store/theme")
def store_theme(db: Session = Depends(get_db), p: Principal = Depends(principal)):
    live = svc.live_theme(db, p.store.id)
    if live is None:
        manifest, _ = require_manifest(starter(DEFAULT_THEME_ID))
        return _public_theme(db, p, manifest, None, None)
    t, rev = live
    return _public_theme(db, p, rev.manifest, t, rev)


@router.get("/store/pages/{slug}")
def store_page(slug: str, db: Session = Depends(get_db), p: Principal = Depends(principal)):
    found = svc.published_doc(db, p.store.id, "page", slug.lower())
    if found is None:
        from ...core.errors import NotFound

        raise NotFound("Page")
    page, rev = found
    root = svc.prune_for_visitor(rev.document.get("root"), dt.datetime.now(dt.UTC), svc.visitor_tags(db, p))
    return {"slug": page.slug, "title": rev.title, "seo": rev.seo,
            "document": {"schemaVersion": rev.document.get("schemaVersion", 1), "root": root},
            "revision": rev.number, "published_at": page.published_at.isoformat() if page.published_at else None}


__all__ = ["router", "MODULE", "resolve_principal"]
