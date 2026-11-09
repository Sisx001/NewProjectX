"""AI tool broker.

Tools are typed (JSON-schema inputs), permission-scoped and classified by side effect:
* read      — executes directly when the acting principal holds `permission` and the agent is assigned the tool;
* write     — creates an AiActionRequest with a dry-run preview; approver needs ai.approve + `permission`;
* sensitive — like write, but only an owner (`*`) may approve.

Effective permission = acting principal's permissions ∩ agent's tool list. Tool results are data for the model and can
never grant permissions. Tools for modules that are not installed report `unavailable` instead of failing silently.
"""
from __future__ import annotations

import datetime as dt
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ...core.rbac import ALL, has_permission
from ...core.security import consteq, token_hash
from ...core.services import Actor, audit, event_type, redact
from ...db import utcnow
from .schema import SchemaError, validate

SIDE_EFFECTS = ("read", "write", "sensitive")
PUBLIC = "public"  # storefront tools callable without staff permissions

EV_ACTION_REQUESTED = event_type("ai.action_requested", "An AI agent proposed an action that needs approval")
EV_ACTION_EXECUTED = event_type("ai.action_executed", "An approved AI action was executed")


class ToolUnavailable(Exception):
    """The capability behind a tool is not installed or not configured."""


@dataclass
class ToolContext:
    db: Session
    store_id: uuid.UUID
    permissions: frozenset
    actor: Actor
    user_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    agent_name: str = ""
    run_id: uuid.UUID | None = None
    channel: str = "admin"
    knowledge_source_ids: list = field(default_factory=list)

    def can(self, perm: str) -> bool:
        return perm == PUBLIC or has_permission(self.permissions, perm)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    permission: str
    side_effect: str
    fn: Callable[[ToolContext, dict], dict]
    preview: Callable[[ToolContext, dict], dict] | None = None
    channels: tuple = ("admin",)
    source: str = "core"
    summary: Callable[[dict], str] | None = None

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.input_schema}}

    def dump(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema,
                "permission": self.permission, "side_effect": self.side_effect, "channels": list(self.channels),
                "source": self.source}


TOOLS: dict[str, Tool] = {}
NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def ai_tool(name: str, description: str, input_schema: dict, permission: str, side_effect: str, *,
            preview: Callable | None = None, channels: tuple = ("admin",), summary: Callable | None = None):
    if side_effect not in SIDE_EFFECTS:
        raise ValueError(f"side_effect must be one of {SIDE_EFFECTS}")
    if not NAME_RE.match(name):
        raise ValueError(f"Invalid tool name {name}")
    if name in TOOLS:
        raise RuntimeError(f"Duplicate AI tool {name}")
    if side_effect != "read" and permission == PUBLIC:
        raise ValueError("Public tools must be read-only")

    def deco(fn):
        TOOLS[name] = Tool(name, description, input_schema, permission, side_effect, fn, preview, channels,
                           summary=summary)
        return fn

    return deco


def all_tools(db: Session, store_id) -> dict[str, Tool]:
    """Core tools plus tools contributed by enabled extensions."""
    out = dict(TOOLS)
    try:
        from ..plugins.service import plugin_tools

        out.update(plugin_tools(db, store_id))
    except ImportError:
        pass
    return out


def obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


# ---------------------------------------------------------------------------------------------- broker
def invoke(ctx: ToolContext, agent, name: str, raw_args: Any, *, mode: str = "chat") -> dict:
    """Run one tool call for the model. Returns {"status", "result", "action_request_id"?}."""
    from .models import AiActionRequest

    tools = all_tools(ctx.db, ctx.store_id)
    tool = tools.get(name)
    if tool is None:
        return {"status": "unknown", "result": {"error": f"No tool named {name}"}}
    if name not in (agent.allowed_tools or []):
        return {"status": "denied", "result": {"error": f"Tool {name} is not assigned to this assistant"}}
    if ctx.channel not in tool.channels:
        return {"status": "denied", "result": {"error": f"Tool {name} is not available in the {ctx.channel} channel"}}
    if not ctx.can(tool.permission):
        return {"status": "denied",
                "result": {"error": f"Permission {tool.permission} is required for {name}; the current user lacks it"}}
    if not isinstance(raw_args, dict):
        return {"status": "invalid", "result": {"error": "Tool arguments must be a JSON object"}}
    try:
        args = validate(tool.input_schema, raw_args)
    except SchemaError as exc:
        return {"status": "invalid", "result": {"error": str(exc)}}
    try:
        # Savepoint: a failing tool query must not poison the run's transaction.
        with ctx.db.begin_nested():
            if tool.side_effect == "read":
                return {"status": "ok", "result": tool.fn(ctx, args)}
            preview = tool.preview(ctx, args) if tool.preview else {"arguments": args}
    except ToolUnavailable as exc:
        return {"status": "unavailable", "result": {"error": str(exc)}}
    except Exception as exc:  # noqa: BLE001 - surfaced to the model and the trace
        return {"status": "error", "result": {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}}
    if mode == "eval":
        return {"status": "approval_required", "result": {"message": "Would request approval (evaluation mode)",
                                                          "preview": preview}}
    summary = tool.summary(args) if tool.summary else f"{name}"
    req = AiActionRequest(store_id=ctx.store_id, agent_id=ctx.agent_id, run_id=ctx.run_id, tool=name,
                          side_effect=tool.side_effect, permission=tool.permission, arguments=args, preview=preview,
                          summary=summary[:500], requested_by=ctx.user_id,
                          requested_permissions=sorted(ctx.permissions),
                          expires_at=utcnow() + dt.timedelta(days=7))
    ctx.db.add(req)
    ctx.db.flush()
    from ...core.services import emit

    emit(ctx.db, ctx.store_id, EV_ACTION_REQUESTED, {"action_request_id": str(req.id), "tool": name,
                                                      "side_effect": tool.side_effect})
    try:
        from ..platform.api import notify

        notify(ctx.db, ctx.store_id, "ai.approval", f"AI action needs approval: {summary[:150]}",
               link=f"/admin/ai/approvals?id={req.id}", permission="ai.approve",
               severity="warning" if tool.side_effect == "sensitive" else "info")
    except ImportError:
        pass
    audit(ctx.db, ctx.store_id, ctx.actor, "ai.action_requested", "ai_action_request", req.id,
          {"tool": name, "side_effect": tool.side_effect})
    who = "an owner" if tool.side_effect == "sensitive" else "a staff member with ai.approve"
    return {"status": "approval_required", "action_request_id": str(req.id),
            "result": {"message": f"Queued for approval by {who}. Nothing has been changed yet.",
                       "action_request_id": str(req.id), "preview": preview}}


# ---------------------------------------------------------------------------------------------- helpers
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def table_columns(db: Session, table: str) -> set[str]:
    rows = db.execute(text("SELECT column_name FROM information_schema.columns "
                           "WHERE table_schema = current_schema() AND table_name = :t"), {"t": table}).scalars().all()
    return {r for r in rows if _IDENT.match(r)}


def _first(cols: set[str], *names: str) -> str | None:
    return next((n for n in names if n in cols), None)


def mask_email(v: str | None) -> str | None:
    if not v or "@" not in v:
        return v
    name, dom = v.split("@", 1)
    return f"{name[:1]}***@{dom}"


def mask_phone(v: str | None) -> str | None:
    return ("•••" + v[-3:]) if v and len(v) > 3 else v


def _orders_cols(db: Session) -> set[str]:
    cols = table_columns(db, "orders")
    if not cols or "store_id" not in cols:
        raise ToolUnavailable("Orders are unavailable: the commerce module is not installed on this version")
    return cols


def _order_row(ctx: ToolContext, r: dict, cols: set[str]) -> dict:
    pii = ctx.can("customers.pii")
    out = {}
    for k in ("number", "status", "payment_status", "fulfillment_status", "total_minor", "subtotal_minor",
              "shipping_minor", "discount_minor", "currency", "payment_method", "placed_at", "created_at"):
        if k in r:
            out[k] = r[k].isoformat() if isinstance(r[k], dt.datetime) else r[k]
    for k in ("email", "customer_email"):
        if k in r:
            out["email"] = r[k] if pii else mask_email(r[k])
    for k in ("phone", "customer_phone"):
        if k in r:
            out["phone"] = r[k] if pii else mask_phone(r[k])
    for k in ("name", "customer_name"):
        if k in r and r[k]:
            out["customer_name"] = r[k] if pii else r[k][:1] + "."
    if not pii:
        out["pii_masked"] = True
    return out


def _select_cols(cols: set[str]) -> str:
    wanted = ["number", "status", "payment_status", "fulfillment_status", "total_minor", "subtotal_minor",
              "shipping_minor", "discount_minor", "currency", "payment_method", "placed_at", "created_at", "email",
              "customer_email", "phone", "customer_phone", "name", "customer_name"]
    return ", ".join(f'"{c}"' for c in wanted if c in cols) or '"store_id"'


# ---------------------------------------------------------------------------------------------- core tools
@ai_tool("system_capabilities", "Summarise what this VOLTORA installation can do: modules, events, AI tools and "
         "extensions, plus the current user's permission scope.", obj({}), "ai.use", "read")
def system_capabilities(ctx: ToolContext, args: dict) -> dict:
    from ...capabilities import capability_summary

    return capability_summary(ctx.db, ctx.store_id, ctx.permissions)


@ai_tool("catalog_search_products", "Search products by title, SKU or tag (staff view, includes drafts).",
         obj({"query": {"type": "string", "maxLength": 120}, "status": {"type": "string",
              "enum": ["draft", "active", "archived"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 25}}),
         "catalog.read", "read")
def catalog_search_products(ctx: ToolContext, args: dict) -> dict:
    return {"items": _search_products(ctx.db, ctx.store_id, args.get("query", ""), args.get("status"),
                                      args.get("limit", 10))}


def _search_products(db: Session, store_id, q: str, status: str | None, limit: int, public: bool = False) -> list:
    from ..catalog.models import Product, Variant

    stmt = (select(Product, func.min(Variant.price_minor), func.max(Variant.price_minor), func.count(Variant.id))
            .outerjoin(Variant, (Variant.product_id == Product.id) & Variant.is_active)
            .where(Product.store_id == store_id).group_by(Product.id))
    if public:
        stmt = stmt.where(Product.status == "active")
    elif status:
        stmt = stmt.where(Product.status == status)
    if q:
        like = f"%{q.strip()[:120]}%"
        stmt = stmt.where(Product.title.ilike(like) | Product.slug.ilike(like) |
                          Product.id.in_(select(Variant.product_id).where(Variant.sku.ilike(like))))
    rows = db.execute(stmt.order_by(Product.updated_at.desc()).limit(limit)).all()
    out = []
    for p, lo, hi, n in rows:
        item = {"slug": p.slug, "title": p.title, "price_minor": lo, "price_max_minor": hi, "variants": n,
                "url": f"/products/{p.slug}"}
        if not public:
            item |= {"id": str(p.id), "status": p.status, "is_demo": p.is_demo}
        out.append(item)
    return out


def _product_detail(db: Session, store_id, slug: str, public: bool) -> dict:
    from ..catalog.models import Product, Variant
    from ..inventory.models import StockLevel

    p = db.execute(select(Product).where(Product.store_id == store_id, Product.slug == slug)).scalar_one_or_none()
    if p is None or (public and p.status != "active"):
        return {"found": False}
    vs = db.execute(select(Variant).where(Variant.product_id == p.id, Variant.is_active)
                    .order_by(Variant.position)).scalars().all()
    stock = dict(db.execute(select(StockLevel.variant_id, func.sum(StockLevel.on_hand - StockLevel.reserved))
                            .where(StockLevel.variant_id.in_([v.id for v in vs])).group_by(StockLevel.variant_id)
                            ).all()) if vs else {}
    variants = []
    for v in vs:
        avail = stock.get(v.id, 0) if v.track_inventory else None
        d = {"sku": v.sku, "title": v.title, "options": v.option_values, "price_minor": v.price_minor,
             "compare_at_minor": v.compare_at_minor,
             "in_stock": (not v.track_inventory) or v.allow_backorder or (avail or 0) > 0}
        if not public:
            d |= {"available": avail, "cost_minor": v.cost_minor}
        variants.append(d)
    # Description is merchant-authored content: returned as data, flagged untrusted by the runtime.
    return {"found": True, "slug": p.slug, "title": p.title, "status": p.status,
            "description": re.sub(r"<[^>]+>", " ", p.description or "")[:2000], "options": p.options,
            "variants": variants, "url": f"/products/{p.slug}"}


@ai_tool("catalog_get_product", "Get one product with variants, prices and stock by slug.",
         obj({"slug": {"type": "string", "minLength": 1, "maxLength": 300}}, ["slug"]), "catalog.read", "read")
def catalog_get_product(ctx: ToolContext, args: dict) -> dict:
    return _product_detail(ctx.db, ctx.store_id, args["slug"], public=False)


@ai_tool("inventory_low_stock", "List variants at or below their reorder point (or a given threshold).",
         obj({"threshold": {"type": "integer", "minimum": 0, "maximum": 100000},
              "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), "inventory.read", "read")
def inventory_low_stock(ctx: ToolContext, args: dict) -> dict:
    return {"items": low_stock(ctx.db, ctx.store_id, args.get("threshold"), args.get("limit", 20))}


def low_stock(db: Session, store_id, threshold: int | None, limit: int) -> list[dict]:
    from ..catalog.models import Product, Variant
    from ..inventory.models import StockLevel

    avail = func.coalesce(func.sum(StockLevel.on_hand - StockLevel.reserved), 0)
    reorder = func.coalesce(func.max(StockLevel.reorder_point), 0)
    stmt = (select(Variant.sku, Variant.title, Product.title, Product.slug, avail, reorder)
            .join(Product, Product.id == Variant.product_id)
            .outerjoin(StockLevel, StockLevel.variant_id == Variant.id)
            .where(Variant.store_id == store_id, Variant.track_inventory, Variant.is_active,
                   Product.status != "archived")
            .group_by(Variant.id, Product.id))
    stmt = stmt.having(avail <= (threshold if threshold is not None else reorder))
    rows = db.execute(stmt.order_by(avail).limit(limit)).all()
    return [{"sku": s, "variant": vt, "product": pt, "slug": slug, "available": int(a), "reorder_point": int(rp)}
            for s, vt, pt, slug, a, rp in rows]


@ai_tool("orders_list", "List recent orders (customer contact data is masked unless you hold customers.pii).",
         obj({"status": {"type": "string", "maxLength": 40}, "limit": {"type": "integer", "minimum": 1,
              "maximum": 25}}), "orders.read", "read")
def orders_list(ctx: ToolContext, args: dict) -> dict:
    cols = _orders_cols(ctx.db)
    order_col = _first(cols, "placed_at", "created_at")
    sql = f'SELECT {_select_cols(cols)} FROM orders WHERE store_id = :s'
    params: dict = {"s": ctx.store_id, "n": args.get("limit", 10)}
    if args.get("status") and "status" in cols:
        sql += " AND status = :st"
        params["st"] = args["status"]
    sql += (f' ORDER BY "{order_col}" DESC' if order_col else "") + " LIMIT :n"
    rows = ctx.db.execute(text(sql), params).mappings().all()
    return {"items": [_order_row(ctx, dict(r), cols) for r in rows]}


@ai_tool("orders_get", "Get one order by its number.", obj({"number": {"type": "string", "minLength": 1,
         "maxLength": 40}}, ["number"]), "orders.read", "read")
def orders_get(ctx: ToolContext, args: dict) -> dict:
    cols = _orders_cols(ctx.db)
    if "number" not in cols:
        raise ToolUnavailable("Orders table has no order number column")
    r = ctx.db.execute(text(f'SELECT {_select_cols(cols)} FROM orders WHERE store_id = :s AND "number" = :n'),
                       {"s": ctx.store_id, "n": args["number"]}).mappings().first()
    return {"found": False} if r is None else {"found": True, "order": _order_row(ctx, dict(r), cols)}


@ai_tool("analytics_sales_summary", "Revenue and order counts for the last N days (excludes cancelled orders).",
         obj({"days": {"type": "integer", "minimum": 1, "maximum": 365}}), "analytics.read", "read")
def analytics_sales_summary(ctx: ToolContext, args: dict) -> dict:
    cols = _orders_cols(ctx.db)
    when = _first(cols, "placed_at", "created_at")
    if not when or "total_minor" not in cols:
        raise ToolUnavailable("Orders table lacks totals or timestamps; sales summary unavailable")
    days = args.get("days", 30)
    where = f'store_id = :s AND "{when}" >= now() - make_interval(days => :d)'
    if "status" in cols:
        where += " AND status NOT IN ('cancelled', 'canceled', 'draft')"
    paid = "count(*) FILTER (WHERE payment_status IN ('paid', 'captured'))" if "payment_status" in cols else "NULL"
    cur = "max(currency)" if "currency" in cols else "NULL"
    r = ctx.db.execute(text(f"SELECT count(*) AS orders, coalesce(sum(total_minor), 0) AS revenue_minor, "
                            f"{paid} AS paid_orders, {cur} AS currency FROM orders WHERE {where}"),
                       {"s": ctx.store_id, "d": days}).mappings().one()
    daily = ctx.db.execute(text(f'SELECT date_trunc(\'day\', "{when}")::date AS day, count(*) AS orders, '
                                f"coalesce(sum(total_minor), 0) AS revenue_minor FROM orders WHERE {where} "
                                "GROUP BY 1 ORDER BY 1"), {"s": ctx.store_id, "d": days}).mappings().all()
    return {"days": days, "orders": r["orders"], "revenue_minor": int(r["revenue_minor"]),
            "paid_orders": r["paid_orders"], "currency": r["currency"],
            "average_order_minor": int(r["revenue_minor"]) // r["orders"] if r["orders"] else 0,
            "daily": [{"day": d["day"].isoformat(), "orders": d["orders"], "revenue_minor": int(d["revenue_minor"])}
                      for d in daily]}


_LOG_SCRUB = [(re.compile(r"(?i)(password|secret|token|api[_-]?key|authorization)(\s*[=:]\s*)\S+"), r"\1\2[redacted]"),
              (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
              (re.compile(r"(?i)bearer\s+[\w.-]+"), "Bearer [redacted]"),
              (re.compile(r"\+?\d[\d -]{8,}\d"), "[phone]")]


def scrub_log(msg: str) -> str:
    for rx, rep in _LOG_SCRUB:
        msg = rx.sub(rep, msg)
    return msg


@ai_tool("system_recent_logs", "Recent warning/error log lines (redacted: secrets, emails and phone numbers removed).",
         obj({"hours": {"type": "integer", "minimum": 1, "maximum": 168}, "level": {"type": "string",
              "enum": ["WARNING", "ERROR"]}, "contains": {"type": "string", "maxLength": 100},
              "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), "system.read", "read")
def system_recent_logs(ctx: ToolContext, args: dict) -> dict:
    from ..platform.models import LogRecord

    since = utcnow() - dt.timedelta(hours=args.get("hours", 24))
    q = select(LogRecord).where(LogRecord.created_at >= since,
                                LogRecord.level.in_([args["level"]] if args.get("level") else ["WARNING", "ERROR",
                                                                                                "CRITICAL"]))
    if args.get("contains"):
        q = q.where(LogRecord.message.ilike(f"%{args['contains']}%"))
    rows = ctx.db.execute(q.order_by(LogRecord.id.desc()).limit(args.get("limit", 20))).scalars().all()
    return {"items": [{"at": r.created_at.isoformat(), "level": r.level, "logger": r.logger,
                       "message": scrub_log(r.message)[:600], "request_id": r.request_id,
                       "data": redact(r.data or {})} for r in rows]}


@ai_tool("system_failed_events", "Explain failed or dead-lettered background events: which subscriber failed, "
         "how often, the error and when it retries.", obj({"limit": {"type": "integer", "minimum": 1, "maximum": 30}}),
         "system.read", "read")
def system_failed_events(ctx: ToolContext, args: dict) -> dict:
    from ...core.services import MAX_ATTEMPTS
    from ..platform.models import OutboxEvent

    rows = ctx.db.execute(select(OutboxEvent).where(OutboxEvent.store_id == ctx.store_id,
                                                    OutboxEvent.status.in_(["failed", "dead"]))
                          .order_by(OutboxEvent.created_at.desc()).limit(args.get("limit", 10))).scalars().all()
    items = []
    for e in rows:
        failing = {k: scrub_log(str(v.get("error", "")))[:300] for k, v in (e.results or {}).items()
                   if not v.get("ok")}
        items.append({"id": str(e.id), "type": e.type, "status": e.status, "attempts": e.attempts,
                      "failing_subscribers": failing,
                      "next_attempt_at": e.next_attempt_at.isoformat() if e.status == "failed" else None,
                      "explanation": (f"Dead-lettered after {e.attempts} attempts (limit {MAX_ATTEMPTS}); staff with "
                                      "system.manage can retry it from System → Events." if e.status == "dead" else
                                      f"Will retry automatically ({e.attempts}/{MAX_ATTEMPTS} attempts so far)."),
                      "created_at": e.created_at.isoformat()})
    return {"items": items, "count": len(items)}


@ai_tool("knowledge_search", "Search the assistant's knowledge sources (store docs, policies, help pages).",
         obj({"query": {"type": "string", "minLength": 2, "maxLength": 300}}, ["query"]), "ai.use", "read")
def knowledge_search_tool(ctx: ToolContext, args: dict) -> dict:
    from .knowledge import search

    hits = search(ctx.db, ctx.store_id, args["query"], ctx.knowledge_source_ids or None, limit=5)
    return {"results": [{"source": h["source"], "citation": h["citation"], "text": h["content"][:1200]}
                        for h in hits]}


# ---- write tools (approval required)
def _discount_table():
    from ...db import Base

    return Base.metadata.tables.get("discounts")


def _discount_row(args: dict, store_id) -> tuple[Any, dict]:
    t = _discount_table()
    if t is None:
        raise ToolUnavailable("Discounts are unavailable: the commerce/discounts module is not installed")
    cols = set(t.c.keys())
    row: dict = {"store_id": store_id}
    for names, val in ((("code",), args["code"].upper()),
                       (("title", "name", "description"), args.get("title") or args["code"].upper()),
                       (("kind", "type", "discount_type"), args["kind"]),
                       (("is_active", "active", "enabled"), False)):
        c = _first(cols, *names)
        if c:
            row[c] = val
    if "status" in cols:
        row["status"] = "draft"
    if args["kind"] == "percentage":
        c = _first(cols, "percent", "percentage", "value", "value_percent")
    else:
        c = _first(cols, "amount_minor", "value_minor", "value")
    if c:
        row[c] = args["value"]
    missing = [c.name for c in t.c if c.name not in row and not c.nullable and c.default is None
               and c.server_default is None and not c.primary_key]
    if missing or not _first(cols, "is_active", "active", "enabled", "status"):
        raise ToolUnavailable(f"Discount schema not recognised (unmapped required columns: {missing}); "
                              "the discounts module must expose a draft API")
    return t, row


def _discount_preview(ctx: ToolContext, args: dict) -> dict:
    hook = _discount_hook()
    if hook is None:
        _discount_row(args, ctx.store_id)
    return {"will_create": "inactive discount draft", "code": args["code"].upper(), "kind": args["kind"],
            "value": args["value"], "note": "Created inactive; a person must activate it."}


def _discount_hook():
    try:
        from ..discounts import service as ds
    except ImportError:
        return None
    return getattr(ds, "create_draft_discount", None)


@ai_tool("marketing_create_discount_draft", "Propose an INACTIVE discount code draft (percentage 1–90 or fixed "
         "amount in minor units). Requires approval; never activates it.",
         obj({"code": {"type": "string", "pattern": "^[A-Za-z0-9_-]{3,32}$"},
              "kind": {"type": "string", "enum": ["percentage", "fixed"]},
              "value": {"type": "integer", "minimum": 1, "maximum": 100_000_000},
              "title": {"type": "string", "maxLength": 160}}, ["code", "kind", "value"]),
         "marketing.write", "write", preview=_discount_preview,
         summary=lambda a: f"Create inactive discount draft {a['code'].upper()} ({a['kind']} {a['value']})")
def marketing_create_discount_draft(ctx: ToolContext, args: dict) -> dict:
    if args["kind"] == "percentage" and args["value"] > 90:
        raise ValueError("Percentage discounts above 90% are not allowed from AI")
    hook = _discount_hook()
    if hook is not None:
        return {"created": hook(ctx.db, ctx.store_id, ctx.actor, code=args["code"].upper(), kind=args["kind"],
                                value=args["value"], title=args.get("title"))}
    t, row = _discount_row(args, ctx.store_id)
    if "id" in t.c and t.c["id"].default is None and "id" not in row:
        row["id"] = uuid.uuid4()
    res = ctx.db.execute(t.insert().values(**row).returning(*[c for c in t.primary_key.columns]))
    pk = res.scalar()
    audit(ctx.db, ctx.store_id, ctx.actor, "marketing.discount_draft_created", "discount", pk,
          {"code": row.get("code"), "active": False})
    return {"created": True, "id": str(pk), "code": args["code"].upper(), "active": False}


def _campaign_preview(ctx: ToolContext, args: dict) -> dict:
    return {"will_create": "stored email campaign draft (not sent)", "subject": args["subject"],
            "language": args.get("language", "en"), "body_preview": args["body"][:500],
            "audience": args.get("audience", "")}


@ai_tool("marketing_draft_email_campaign", "Store an email campaign DRAFT (subject, body, audience note). It is "
         "never sent by AI; a marketer reviews and sends it from Marketing.",
         obj({"subject": {"type": "string", "minLength": 3, "maxLength": 200},
              "body": {"type": "string", "minLength": 10, "maxLength": 20000},
              "audience": {"type": "string", "maxLength": 300},
              "language": {"type": "string", "enum": ["en", "bn"]}}, ["subject", "body"]),
         "marketing.write", "write", preview=_campaign_preview,
         summary=lambda a: f"Save email campaign draft “{a['subject'][:80]}”")
def marketing_draft_email_campaign(ctx: ToolContext, args: dict) -> dict:
    from .models import AiDraft

    d = AiDraft(store_id=ctx.store_id, kind="email_campaign", title=args["subject"], content=args,
                created_by_agent=ctx.agent_id, approved_by=ctx.user_id)
    ctx.db.add(d)
    ctx.db.flush()
    audit(ctx.db, ctx.store_id, ctx.actor, "marketing.campaign_draft_created", "ai_draft", d.id,
          {"subject": args["subject"]})
    return {"draft_id": str(d.id), "sent": False}


def _content_hook():
    try:
        from ..content import service as cs
    except ImportError:
        return None
    return getattr(cs, "create_draft_revision", None)


def _page_preview(ctx: ToolContext, args: dict) -> dict:
    if _content_hook() is None:
        raise ToolUnavailable("Page drafts are unavailable: the content module does not expose draft revisions yet")
    return {"will_create": "draft page revision (not published)", "page": args["page_slug"],
            "change_summary": args["summary"], "document_root_type": args["document"].get("root", {}).get("type")}


@ai_tool("content_propose_page_change", "Propose a change to a storefront page as a DRAFT revision (validated by "
         "the builder; publishing stays a separate human action).",
         obj({"page_slug": {"type": "string", "pattern": "^[a-z0-9-/]{1,120}$"},
              "summary": {"type": "string", "minLength": 3, "maxLength": 300},
              "document": {"type": "object"}}, ["page_slug", "summary", "document"]),
         "content.write", "write", preview=_page_preview,
         summary=lambda a: f"Draft change to page /{a['page_slug']}: {a['summary'][:120]}")
def content_propose_page_change(ctx: ToolContext, args: dict) -> dict:
    hook = _content_hook()
    if hook is None:
        raise ToolUnavailable("Page drafts are unavailable: the content module does not expose draft revisions yet")
    return {"draft": hook(ctx.db, ctx.store_id, ctx.actor, page_slug=args["page_slug"], document=args["document"],
                          note=args["summary"]), "published": False}


def _tag_preview(ctx: ToolContext, args: dict) -> dict:
    from ..customers.models import Customer

    c = ctx.db.get(Customer, uuid.UUID(args["customer_id"]))
    if c is None or c.store_id != ctx.store_id:
        raise ValueError("Customer not found")
    return {"customer": c.name or mask_email(c.email), "tags_before": c.tags,
            "tags_after": sorted({*(c.tags or []), args["tag"]})}


@ai_tool("customers_add_tag", "Add a tag to a customer (segmentation).",
         obj({"customer_id": {"type": "string", "pattern": "^[0-9a-fA-F-]{36}$"},
              "tag": {"type": "string", "pattern": "^[\\w -]{1,40}$"}}, ["customer_id", "tag"]),
         "customers.write", "write", preview=_tag_preview,
         summary=lambda a: f"Tag customer {a['customer_id'][:8]} with “{a['tag']}”")
def customers_add_tag(ctx: ToolContext, args: dict) -> dict:
    from ..customers.models import Customer

    c = ctx.db.get(Customer, uuid.UUID(args["customer_id"]), with_for_update=True)
    if c is None or c.store_id != ctx.store_id:
        raise ValueError("Customer not found")
    c.tags = sorted({*(c.tags or []), args["tag"]})
    audit(ctx.db, ctx.store_id, ctx.actor, "customers.tagged", "customer", c.id, {"tag": args["tag"]})
    return {"tags": c.tags}


def _sessions_preview(ctx: ToolContext, args: dict) -> dict:
    from ..identity.models import AuthSession, Membership, User

    u = ctx.db.get(User, uuid.UUID(args["user_id"]))
    member = u and ctx.db.execute(select(Membership).where(Membership.user_id == u.id,
                                                           Membership.store_id == ctx.store_id)).first()
    if not member:
        raise ValueError("Staff member not found in this store")
    n = ctx.db.execute(select(func.count()).select_from(AuthSession).where(
        AuthSession.user_id == u.id, AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow())).scalar()
    return {"user": u.email, "active_sessions": n, "reason": args["reason"]}


@ai_tool("security_revoke_staff_sessions", "Sign a staff member out everywhere (e.g. suspected compromise). "
         "Sensitive: only an owner can approve.",
         obj({"user_id": {"type": "string", "pattern": "^[0-9a-fA-F-]{36}$"},
              "reason": {"type": "string", "minLength": 3, "maxLength": 300}}, ["user_id", "reason"]),
         "security.manage", "sensitive", preview=_sessions_preview,
         summary=lambda a: f"Revoke all sessions of staff {a['user_id'][:8]}: {a['reason'][:100]}")
def security_revoke_staff_sessions(ctx: ToolContext, args: dict) -> dict:
    from sqlalchemy import update

    from ..identity.models import AuthSession

    _sessions_preview(ctx, args)
    n = ctx.db.execute(update(AuthSession).where(AuthSession.user_id == uuid.UUID(args["user_id"]),
                                                 AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow())
                       ).rowcount
    audit(ctx.db, ctx.store_id, ctx.actor, "security.sessions_revoked", "user", args["user_id"],
          {"count": n, "reason": args["reason"]})
    return {"revoked_sessions": n}


# ---- public storefront tools (read-only, published data only)
@ai_tool("store_search_products", "Search the published catalog.",
         obj({"query": {"type": "string", "maxLength": 120}, "limit": {"type": "integer", "minimum": 1,
              "maximum": 8}}), PUBLIC, "read", channels=("storefront", "admin"))
def store_search_products(ctx: ToolContext, args: dict) -> dict:
    return {"items": _search_products(ctx.db, ctx.store_id, args.get("query", ""), None, args.get("limit", 5),
                                      public=True)}


@ai_tool("store_product_detail", "Details, options, prices and availability of a published product.",
         obj({"slug": {"type": "string", "minLength": 1, "maxLength": 300}}, ["slug"]), PUBLIC, "read",
         channels=("storefront", "admin"))
def store_product_detail(ctx: ToolContext, args: dict) -> dict:
    d = _product_detail(ctx.db, ctx.store_id, args["slug"], public=True)
    d.pop("status", None)
    return d


@ai_tool("store_order_status", "Status of an order. Requires the order number AND the private access token from "
         "the confirmation email/page; returns no personal data.",
         obj({"number": {"type": "string", "minLength": 1, "maxLength": 40},
              "token": {"type": "string", "minLength": 8, "maxLength": 200}}, ["number", "token"]), PUBLIC, "read",
         channels=("storefront",))
def store_order_status(ctx: ToolContext, args: dict) -> dict:
    cols = _orders_cols(ctx.db)
    tok_col = _first(cols, "access_token_hash", "access_token")
    if "number" not in cols or not tok_col:
        raise ToolUnavailable("Order lookup is unavailable on this version")
    want = [c for c in ("status", "payment_status", "fulfillment_status", "placed_at", "created_at") if c in cols]
    sel = ", ".join(f'"{c}"' for c in [*want, tok_col])
    r = ctx.db.execute(text(f'SELECT {sel} FROM orders WHERE store_id = :s AND "number" = :n'),
                       {"s": ctx.store_id, "n": args["number"]}).mappings().first()
    expected = token_hash(args["token"]) if tok_col == "access_token_hash" else args["token"]
    if r is None or not r[tok_col] or not consteq(str(r[tok_col]), expected):
        time.sleep(0.05)
        return {"found": False, "message": "No order matches that number and token"}
    return {"found": True, "number": args["number"],
            **{k: (r[k].isoformat() if isinstance(r[k], dt.datetime) else r[k]) for k in want}}


def approver_can(permissions: frozenset | set, tool: Tool) -> tuple[bool, str]:
    if not has_permission(permissions, "ai.approve"):
        return False, "Approving AI actions requires ai.approve"
    if not has_permission(permissions, tool.permission):
        return False, f"Approving this action requires {tool.permission}"
    if tool.side_effect == "sensitive" and ALL not in permissions:
        return False, "Sensitive AI actions can only be approved by an owner"
    return True, ""
