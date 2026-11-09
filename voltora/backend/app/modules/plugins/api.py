from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, require, staff, store_dep
from ...core.errors import NotFound
from ...core.services import _canon
from ...db import get_db
from . import service as svc
from .manifest import MAX_ZIP_BYTES, compatible, read_package, validate_manifest
from .models import PluginInstallation, PluginPackage

router = APIRouter(prefix="/api/plugins", tags=["plugins"])
store_router = APIRouter(prefix="/api/store", tags=["storefront-extensions"])
extra_routers = [store_router]
MODULE = {"title": "Extensions", "description": "Versioned declarative/remote extensions with consent, contract "
                                                "tests, health, update and rollback"}
READ = require("plugins.read")
MANAGE = require("plugins.manage")


def inst_out(db: Session, i: PluginInstallation, full: bool = False) -> dict:
    m = i.manifest
    out = {"plugin_id": i.plugin_id, "name": i.name, "version": i.version, "status": i.status, "source": i.source,
           "publisher": m.get("publisher"), "description": m.get("description", ""),
           "permissions": i.granted_permissions, "sandbox_status": (i.sandbox or {}).get("status"),
           "health_status": (i.health or {}).get("status"), "previous_version": (i.history or [{}])[-1].get("version")
           if i.history else None, "updated_at": i.updated_at.isoformat() if i.updated_at else None,
           "extension_points": {k: len(v) for k, v in (m.get("extensions") or {}).items() if v}}
    if full:
        out |= {"manifest": m, "settings": i.settings, "settings_schema": m.get("settings"),
                "secrets": [{**s, "configured": s["key"] in (hints := svc.configured_secrets(db, i)),
                             "hint": ("••••" + hints[s["key"]]) if hints.get(s["key"]) else None}
                            for s in m.get("secrets", [])],
                "sandbox": i.sandbox, "health": i.health, "package_sha256": i.package_sha256,
                "history": [{"version": h["version"], "at": h["at"]} for h in i.history or []],
                "readiness": svc.readiness(db, i) if i.status != "uninstalled" else [],
                "allowed_hosts": m.get("allowedHosts", [])}
    return out


def _get(db: Session, p: Principal, plugin_id: str, lock: bool = False) -> PluginInstallation:
    i = svc.get_installation(db, p.store.id, plugin_id, lock=lock)
    if i is None or i.status == "uninstalled":
        raise NotFound("Extension")
    return i


@router.get("/catalog")
def catalog(db: Session = Depends(get_db), p: Principal = Depends(READ)):
    installed = {i.plugin_id: i for i in db.execute(select(PluginInstallation).where(
        PluginInstallation.store_id == p.store.id)).scalars()}
    items = []
    for pid, raw in svc.catalog().items():
        m = validate_manifest(raw)
        i = installed.get(pid)
        items.append({"id": pid, "name": m.name, "version": m.version, "publisher": m.publisher,
                      "description": m.description, "license": m.license, "permissions": m.permissions,
                      "allowed_hosts": m.allowedHosts, "compatible": compatible(m), "verified_publisher": True,
                      "extension_points": {k: len(v) for k, v in m.extensions.model_dump().items() if v},
                      "installed_version": i.version if i and i.status != "uninstalled" else None,
                      "status": i.status if i else None,
                      "update_available": bool(i and i.status != "uninstalled" and svc._newer(m.version, i.version))})
    return {"items": items}


@router.get("/installed")
def installed(db: Session = Depends(get_db), p: Principal = Depends(READ)):
    rows = db.execute(select(PluginInstallation).where(PluginInstallation.store_id == p.store.id,
                                                       PluginInstallation.status != "uninstalled")
                      .order_by(PluginInstallation.name)).scalars().all()
    return {"items": [inst_out(db, i) for i in rows], "updates": svc.update_available(db, p.store.id)}


@router.get("/installed/{plugin_id}")
def installed_detail(plugin_id: str, db: Session = Depends(get_db), p: Principal = Depends(READ)):
    return inst_out(db, _get(db, p, plugin_id), full=True)


class CatalogPick(BaseModel):
    catalog_id: str = Field(max_length=100)


@router.post("/packages")
def package_from_catalog(body: CatalogPick, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    raw = svc.catalog().get(body.catalog_id)
    if raw is None:
        raise NotFound("Catalog extension")
    pkg = svc.create_package(db, p.store.id, raw, "catalog", hashlib.sha256(_canon(raw).encode()).hexdigest(),
                             p.actor)
    db.commit()
    return svc.preview(db, p.store.id, pkg, p.permissions)


@router.post("/packages/upload")
async def package_upload(file: UploadFile = File(...), db: Session = Depends(get_db),
                         p: Principal = Depends(MANAGE)):
    data = await file.read(MAX_ZIP_BYTES + 1)
    raw, sha, readme = read_package(file.filename or "package", data)
    pkg = svc.create_package(db, p.store.id, raw, "upload", sha, p.actor, filename=(file.filename or "")[:200],
                             readme=readme)
    db.commit()
    return svc.preview(db, p.store.id, pkg, p.permissions)


def _pkg(db: Session, p: Principal, pkg_id: str) -> PluginPackage:
    pkg = db.get(PluginPackage, parse_uuid(pkg_id, "Package"))
    if pkg is None or pkg.store_id != p.store.id:
        raise NotFound("Package")
    return pkg


@router.get("/packages/{pkg_id}")
def package_preview(pkg_id: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    return svc.preview(db, p.store.id, _pkg(db, p, pkg_id), p.permissions)


class Consent(BaseModel):
    granted_permissions: list[str] = Field(default_factory=list, max_length=40)


@router.post("/packages/{pkg_id}/install")
def install(pkg_id: str, body: Consent, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    inst, signing = svc.install(db, p.store.id, _pkg(db, p, pkg_id), body.granted_permissions, p.actor,
                                p.permissions)
    db.commit()
    return {**inst_out(db, inst, full=True), "signing_secret": signing,
            "note": "If this extension calls a publisher-operated service, give it this signing secret so it can "
                    "verify VOLTORA's requests. It will not be shown again."}


class UpdateIn(Consent):
    package_id: str


@router.post("/installed/{plugin_id}/update")
def update(plugin_id: str, body: UpdateIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    inst = _get(db, p, plugin_id, lock=True)
    svc.update(db, p.store.id, inst, _pkg(db, p, body.package_id), body.granted_permissions, p.actor, p.permissions)
    db.commit()
    return inst_out(db, inst, full=True)


@router.post("/installed/{plugin_id}/rollback")
def rollback(plugin_id: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    inst = _get(db, p, plugin_id, lock=True)
    svc.rollback(db, p.store.id, inst, p.actor)
    db.commit()
    return inst_out(db, inst, full=True)


@router.post("/installed/{plugin_id}/enable")
def enable(plugin_id: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    inst = _get(db, p, plugin_id, lock=True)
    svc.enable(db, inst, p.actor)
    db.commit()
    return inst_out(db, inst, full=True)


@router.post("/installed/{plugin_id}/disable")
def disable(plugin_id: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    inst = _get(db, p, plugin_id, lock=True)
    svc.disable(db, inst, p.actor)
    db.commit()
    return inst_out(db, inst, full=True)


@router.delete("/installed/{plugin_id}")
def uninstall(plugin_id: str, purge: bool = False, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    inst = _get(db, p, plugin_id, lock=True)
    svc.uninstall(db, p.store.id, inst, p.actor, purge)
    db.commit()
    return {"ok": True, "data": "purged" if purge else "kept for reinstall"}


class SettingsIn(BaseModel):
    values: dict


@router.put("/installed/{plugin_id}/settings")
def put_settings(plugin_id: str, body: SettingsIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    inst = _get(db, p, plugin_id, lock=True)
    svc.update_settings(db, inst, body.values, p.actor)
    db.commit()
    return inst_out(db, inst, full=True)


class SecretsIn(BaseModel):
    values: dict[str, str | None]


@router.put("/installed/{plugin_id}/secrets")
def put_secrets(plugin_id: str, body: SecretsIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    inst = _get(db, p, plugin_id, lock=True)
    svc.update_secrets(db, inst, body.values, p.actor)
    db.commit()
    return inst_out(db, inst, full=True)


@router.post("/installed/{plugin_id}/sandbox-test")
def sandbox_test(plugin_id: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    from ...core.services import audit

    inst = _get(db, p, plugin_id, lock=True)
    inst.sandbox = svc.sandbox_test(db, inst)
    audit(db, p.store.id, p.actor, "plugins.sandbox_tested", "plugin", plugin_id, {"status": inst.sandbox["status"]})
    db.commit()
    return inst.sandbox


@router.post("/installed/{plugin_id}/health")
def health(plugin_id: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    inst = _get(db, p, plugin_id, lock=True)
    out = svc.health_check(db, inst)
    db.commit()
    return {**out, "plugin_status": inst.status}


@router.post("/installed/{plugin_id}/rotate-signing-secret")
def rotate(plugin_id: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    from ...core.services import audit

    inst = _get(db, p, plugin_id, lock=True)
    raw = svc._new_signing_secret(db, p.store.id, inst.plugin_id)
    audit(db, p.store.id, p.actor, "plugins.signing_secret_rotated", "plugin", plugin_id)
    db.commit()
    return {"signing_secret": raw}


@router.get("/admin-links")
def links(db: Session = Depends(get_db), p: Principal = Depends(staff)):
    return {"items": svc.admin_links(db, p.store.id)}


@store_router.get("/plugin-components")
def plugin_components(db: Session = Depends(get_db), store=Depends(store_dep)):
    """Declarative builder components and storefront widgets from enabled extensions (render with core runtime)."""
    return svc.storefront_components(db, store.id)


