"""AI capability graph: derived at runtime from the mounted API, permission catalog, settings registry,
event catalog, AI tool registry and installed extensions. Nothing here is a static marketing list."""
from __future__ import annotations

from fastapi.routing import APIRoute

from .config import CORE_VERSION, HOST_API_VERSION, get_settings
from .core.rbac import ALL, PERMISSIONS, has_permission
from .core.services import EVENT_TYPES, SETTINGS

MODULES: dict[str, dict] = {}  # filled by app discovery: name -> {"title", "description", "routes"}
ROUTERS: list = []  # original APIRouters (FastAPI >=0.16x wraps included routers lazily)


def route_permissions(route: APIRoute) -> list[str]:
    perms = []
    for dep in route.dependant.dependencies:
        name = getattr(dep.call, "__name__", "")
        if name.startswith("require_"):
            perms.extend(p for p in PERMISSIONS if "require_" + p.replace(".", "_") in name)
        elif name == "staff":
            perms.append("staff")
        elif name in ("customer", "signed_in"):
            perms.append("signed_in")
    return sorted(set(perms))


def _with_session(db, fn):
    if db is not None:
        return fn(db)
    from .db import session_factory

    with session_factory()() as s:
        return fn(s)


def _tools(db, store_id, perms) -> list[dict]:
    try:
        from .modules.ai.tools import PUBLIC, all_tools
    except ImportError:
        return []
    tools = _with_session(db, lambda s: all_tools(s, store_id))
    return [{"name": t.name, "description": t.description, "permission": t.permission,
             "side_effect": t.side_effect, "source": t.source,
             "allowed_for_you": t.permission == PUBLIC or has_permission(perms, t.permission)}
            for t in tools.values()]


def _plugins(db, store_id) -> list[dict]:
    try:
        from .modules.plugins.service import installed_summary
    except ImportError:
        return []
    return _with_session(db, lambda s: installed_summary(s, store_id))


def build_capability_graph(app=None, principal=None, db=None) -> dict:
    """`app` is accepted for backwards compatibility; routes come from ROUTERS so no Request is needed."""
    perms = frozenset(principal.permissions) if principal is not None else frozenset()
    is_staff = bool(principal is not None and principal.is_staff)
    store_id = principal.store.id if principal is not None else None
    routes = []
    for r in [rt for router in ROUTERS for rt in router.routes]:
        if isinstance(r, APIRoute) and r.path.startswith("/api/"):
            rp = route_permissions(r)
            staff_perms = [x for x in rp if x in PERMISSIONS]
            routes.append({"path": r.path, "methods": sorted(r.methods - {"HEAD"}), "name": r.name,
                           "tags": r.tags, "requires": rp,
                           "allowed_for_you": all(has_permission(perms, x) for x in staff_perms)
                           and ("staff" not in rp or is_staff)})
    s = get_settings()
    return {
        "version": CORE_VERSION,
        "host_api_version": HOST_API_VERSION,
        "environment": {"env": s.env, "payments_mode": s.payments_mode, "external_network": s.external_network},
        "modules": MODULES,
        "permissions": PERMISSIONS,
        "your_permissions": sorted(perms),
        "settings": {k: {"label": d.label, "group": d.group, "type": d.type} for k, d in SETTINGS.items()},
        "events": EVENT_TYPES,
        "ai_tools": _tools(db, store_id, perms) if store_id else [],
        "plugins": _plugins(db, store_id) if store_id else [],
        "routes": routes,
    }


def capability_summary(db, store_id, permissions, compact: bool = False) -> dict:
    """Prompt-sized view for assistants: what exists, what is enabled, and what the caller may use."""
    perms = frozenset(permissions)
    s = get_settings()
    tools = _tools(db, store_id, perms)
    plugins = _plugins(db, store_id)
    route_counts: dict[str, int] = {}
    for r in [rt for router in ROUTERS for rt in router.routes]:
        if isinstance(r, APIRoute) and r.path.startswith("/api/"):
            key = (r.tags or ["other"])[0]
            route_counts[key] = route_counts.get(key, 0) + 1
    out = {
        "version": CORE_VERSION,
        "environment": {"env": s.env, "payments_mode": s.payments_mode,
                        "external_network": s.external_network},
        "modules": {k: v.get("title", k) for k, v in MODULES.items()},
        "api_routes_by_area": route_counts,
        "permission_scope": "owner (all permissions)" if ALL in perms else sorted(perms),
        "ai_tools_you_can_use": [t["name"] for t in tools if t["allowed_for_you"]],
        "ai_tools_not_permitted": [t["name"] for t in tools if not t["allowed_for_you"]],
        "extensions": [{"id": p["id"], "version": p["version"], "status": p["status"]} for p in plugins],
        "event_types": sorted(EVENT_TYPES) if compact else EVENT_TYPES,
    }
    if not compact:
        out["settings_groups"] = sorted({d.group for d in SETTINGS.values()})
    return out
