"""AI capability graph: derived at runtime from the mounted API, permission catalog, settings registry,
event catalog, AI tool registry and installed extensions. Nothing here is a static marketing list."""
from __future__ import annotations

from fastapi.routing import APIRoute

from .config import CORE_VERSION, get_settings
from .core.rbac import PERMISSIONS
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


def build_capability_graph(app, principal) -> dict:
    routes = []
    for r in [rt for router in ROUTERS for rt in router.routes]:
        if isinstance(r, APIRoute) and r.path.startswith("/api/"):
            perms = route_permissions(r)
            staff_perms = [x for x in perms if x in PERMISSIONS]
            routes.append({"path": r.path, "methods": sorted(r.methods - {"HEAD"}), "name": r.name,
                           "tags": r.tags, "requires": perms,
                           "allowed_for_you": all(principal.can(x) for x in staff_perms)
                           and ("staff" not in perms or principal.is_staff)})
    tools = []
    try:
        from .modules.ai.tools import TOOLS

        tools = [{"name": t.name, "description": t.description, "permission": t.permission,
                  "side_effect": t.side_effect, "allowed_for_you": principal.can(t.permission)}
                 for t in TOOLS.values()]
    except ImportError:
        pass
    plugins = []
    try:
        from .modules.plugins.service import installed_summary

        plugins = installed_summary(principal.store.id)
    except ImportError:
        pass
    s = get_settings()
    return {
        "version": CORE_VERSION,
        "environment": {"env": s.env, "payments_mode": s.payments_mode, "external_network": s.external_network},
        "modules": MODULES,
        "permissions": PERMISSIONS,
        "your_permissions": sorted(principal.permissions),
        "settings": {k: {"label": d.label, "group": d.group, "type": d.type} for k, d in SETTINGS.items()},
        "events": EVENT_TYPES,
        "ai_tools": tools,
        "plugins": plugins,
        "routes": routes,
    }
