from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.deps import Principal, parse_uuid, principal, require
from ...core.errors import AppError, Conflict, Forbidden, NotFound
from ...core.security import new_token, token_hash
from ...core.services import audit, limiter
from ...db import get_db, utcnow
from . import knowledge as kn
from . import providers as prov
from .models import (
    AiActionRequest,
    AiAgent,
    AiAgentVersion,
    AiConversation,
    AiDraft,
    AiEvalCase,
    AiEvalResult,
    AiKnowledgeChunk,
    AiKnowledgeSource,
    AiMemory,
    AiMessage,
    AiModelPrice,
    AiProvider,
    AiRun,
    AiTask,
    AiToolCall,
)
from .runtime import ROLE_LABELS, Caller, ChatFailed, month_start, run_chat, spent_micros
from .service import TEMPLATES, agent_config, decide, memory_expiry, run_evals
from .tools import PUBLIC, all_tools

router = APIRouter(prefix="/api/ai", tags=["ai"])
store_router = APIRouter(prefix="/api/store/assistant", tags=["storefront-ai"])
extra_routers = [store_router]
MODULE = {"title": "AI Studio", "description": "Providers, agents, permission-scoped tools, approvals, knowledge, "
                                              "evals and usage"}
MANAGE = require("ai.manage")
USE = require("ai.use")


def iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v else None


# ================================================================ providers
def provider_out(db: Session, p: AiProvider) -> dict:
    from ..platform.models import SecretRecord

    hint = db.execute(select(SecretRecord.hint).where(SecretRecord.store_id == p.store_id,
                                                      SecretRecord.ref == prov.key_ref(p.id))).scalar()
    return {"id": str(p.id), "name": p.name, "preset": p.preset, "base_url": p.base_url,
            "default_model": p.default_model, "enabled": p.enabled, "status": p.status, "models": p.models,
            "last_test_at": iso(p.last_test_at), "last_error": p.last_error, "timeout_seconds": p.timeout_seconds,
            "max_retries": p.max_retries, "requests_per_minute": p.requests_per_minute,
            "supports_tools": p.supports_tools, "has_key": hint is not None,
            "key_hint": ("••••" + hint) if hint else None}


class ProviderIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    preset: str = "custom"
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=500)
    default_model: str = Field("", max_length=160)
    enabled: bool = True
    timeout_seconds: int = Field(60, ge=5, le=300)
    max_retries: int = Field(2, ge=0, le=5)
    requests_per_minute: int = Field(60, ge=1, le=6000)
    supports_tools: bool = True


class ProviderPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=500)  # "" clears the key
    default_model: str | None = Field(None, max_length=160)
    enabled: bool | None = None
    timeout_seconds: int | None = Field(None, ge=5, le=300)
    max_retries: int | None = Field(None, ge=0, le=5)
    requests_per_minute: int | None = Field(None, ge=1, le=6000)
    supports_tools: bool | None = None


def _get_provider(db: Session, p: Principal, pid: str) -> AiProvider:
    x = prov.get_provider(db, p.store.id, parse_uuid(pid, "Provider"))
    if x is None:
        raise NotFound("Provider")
    return x


def _store_key(db: Session, p: Principal, x: AiProvider, key: str | None) -> None:
    from ..platform.models import SecretRecord
    from ..webhooks.service import new_secret

    if key is None:
        return
    if key == "":
        db.query(SecretRecord).filter(SecretRecord.store_id == p.store.id,
                                      SecretRecord.ref == prov.key_ref(x.id)).delete()
        return
    new_secret(db, p.store.id, prov.key_ref(x.id), owner_kind="ai", owner_ref=str(x.id), value=key.strip())


@router.get("/presets")
def presets(p: Principal = Depends(USE)):
    from ...config import get_settings

    allow = get_settings().ai_allow_private_endpoints
    return {"items": [{"key": k, **v, "available": allow or not v["private"],
                       "unavailable_reason": None if allow or not v["private"] else
                       "Self-hosted endpoints need VOLTORA_AI_ALLOW_PRIVATE_ENDPOINTS=true"}
                      for k, v in prov.PRESETS.items()]}


@router.get("/providers")
def list_providers(db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    rows = db.execute(select(AiProvider).where(AiProvider.store_id == p.store.id).order_by(AiProvider.created_at)
                      ).scalars().all()
    return {"items": [provider_out(db, x) for x in rows]}


@router.post("/providers")
def create_provider(body: ProviderIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    preset = prov.PRESETS.get(body.preset)
    if preset is None:
        raise AppError("unknown_preset", f"Unknown provider preset {body.preset}")
    url = prov.validate_base_url(body.base_url or preset["base_url"])
    x = AiProvider(id=uuid.uuid4(), store_id=p.store.id, name=body.name, preset=body.preset, base_url=url,
                   default_model=body.default_model, enabled=body.enabled, timeout_seconds=body.timeout_seconds,
                   max_retries=body.max_retries, requests_per_minute=body.requests_per_minute,
                   supports_tools=body.supports_tools)
    db.add(x)
    db.flush()
    _store_key(db, p, x, body.api_key or None)
    audit(db, p.store.id, p.actor, "ai.provider_created", "ai_provider", x.id,
          {"preset": body.preset, "base_url": url, "has_key": bool(body.api_key)})
    db.commit()
    return provider_out(db, x)


@router.patch("/providers/{pid}")
def update_provider(pid: str, body: ProviderPatch, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    x = _get_provider(db, p, pid)
    changes = body.model_dump(exclude_none=True, exclude={"api_key"})
    if "base_url" in changes:
        changes["base_url"] = prov.validate_base_url(changes["base_url"])
    for k, v in changes.items():
        setattr(x, k, v)
    if "base_url" in changes or body.api_key is not None:
        x.status, x.models = "untested", []
    _store_key(db, p, x, body.api_key)
    audit(db, p.store.id, p.actor, "ai.provider_updated", "ai_provider", x.id,
          {**changes, "key_changed": body.api_key is not None})
    db.commit()
    return provider_out(db, x)


@router.delete("/providers/{pid}")
def delete_provider(pid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    from ..platform.models import SecretRecord

    x = _get_provider(db, p, pid)
    for a in db.execute(select(AiAgent).where(AiAgent.provider_id == x.id)).scalars():
        a.provider_id, a.enabled = None, False
    db.query(SecretRecord).filter(SecretRecord.store_id == p.store.id,
                                  SecretRecord.ref == prov.key_ref(x.id)).delete()
    db.delete(x)
    audit(db, p.store.id, p.actor, "ai.provider_deleted", "ai_provider", x.id)
    db.commit()
    return {"ok": True}


@router.post("/providers/{pid}/test")
def test_provider(pid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    x = _get_provider(db, p, pid)
    limiter.hit(f"ai_test:{x.id}", 10, 60)
    x.last_test_at = utcnow()
    try:
        x.models = prov.list_models(db, x)
        x.status, x.last_error = "ok", None
    except prov.ProviderError as exc:
        x.status, x.last_error = "error", exc.message
    audit(db, p.store.id, p.actor, "ai.provider_tested", "ai_provider", x.id, {"status": x.status})
    db.commit()
    out = provider_out(db, x)
    out["test"] = {"ok": x.status == "ok", "error": x.last_error, "model_count": len(x.models),
                   "default_model_listed": (x.default_model in x.models) if x.default_model and x.models else None}
    return out


class PriceIn(BaseModel):
    model: str = Field(min_length=1, max_length=160)
    input_micros_per_mtok: int = Field(ge=0, le=10**12)
    output_micros_per_mtok: int = Field(ge=0, le=10**12)
    currency: str = Field("USD", pattern="^[A-Z]{3}$")


class PricesIn(BaseModel):
    items: list[PriceIn] = Field(max_length=200)


def price_out(r: AiModelPrice) -> dict:
    return {"model": r.model, "input_micros_per_mtok": r.input_micros_per_mtok,
            "output_micros_per_mtok": r.output_micros_per_mtok, "currency": r.currency}


@router.get("/providers/{pid}/prices")
def get_prices(pid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    x = _get_provider(db, p, pid)
    rows = db.execute(select(AiModelPrice).where(AiModelPrice.provider_id == x.id).order_by(AiModelPrice.model)
                      ).scalars().all()
    return {"items": [price_out(r) for r in rows],
            "note": "Prices are entered by you from your provider's price list; VOLTORA never assumes prices."}


@router.put("/providers/{pid}/prices")
def put_prices(pid: str, body: PricesIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    x = _get_provider(db, p, pid)
    db.query(AiModelPrice).filter(AiModelPrice.provider_id == x.id).delete()
    seen = set()
    for it in body.items:
        if it.model in seen:
            raise AppError("duplicate_model", f"{it.model} is listed twice")
        seen.add(it.model)
        db.add(AiModelPrice(store_id=p.store.id, provider_id=x.id, **it.model_dump()))
    audit(db, p.store.id, p.actor, "ai.prices_updated", "ai_provider", x.id, {"models": sorted(seen)})
    db.commit()
    return get_prices(pid, db, p)


# ================================================================ agents
def agent_out(a: AiAgent, full: bool = True) -> dict:
    out = {"id": str(a.id), "key": a.key, "name": a.name, "role": a.role, "role_label": ROLE_LABELS.get(a.role, a.role),
           "description": a.description, "channel": a.channel, "enabled": a.enabled, "builtin": a.builtin,
           "provider_id": str(a.provider_id) if a.provider_id else None, "model": a.model, "version": a.version}
    if full:
        out |= {"fallback_model": a.fallback_model, "temperature": a.temperature, "max_steps": a.max_steps,
                "max_output_tokens": a.max_output_tokens, "system_prompt": a.system_prompt,
                "allowed_tools": a.allowed_tools, "knowledge_source_ids": a.knowledge_source_ids,
                "memory_policy": a.memory_policy, "memory_retention_days": a.memory_retention_days,
                "monthly_budget_micros": a.monthly_budget_micros, "updated_at": iso(a.updated_at)}
    return out


class AgentIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    role: str
    description: str = Field("", max_length=1000)
    channel: str = Field("admin", pattern="^(admin|storefront)$")
    provider_id: str | None = None
    model: str = Field("", max_length=160)
    fallback_model: str = Field("", max_length=160)
    temperature: float = Field(0.3, ge=0, le=2)
    max_steps: int = Field(5, ge=1, le=12)
    max_output_tokens: int = Field(1200, ge=64, le=32000)
    system_prompt: str = Field("", max_length=20000)
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    knowledge_source_ids: list[str] = Field(default_factory=list, max_length=100)
    memory_policy: str = Field("conversation", pattern="^(none|conversation|long_term)$")
    memory_retention_days: int = Field(30, ge=0, le=3650)
    monthly_budget_micros: int | None = Field(None, ge=0)
    enabled: bool = False


class AgentPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)
    channel: str | None = Field(None, pattern="^(admin|storefront)$")
    provider_id: str | None = None
    model: str | None = Field(None, max_length=160)
    fallback_model: str | None = Field(None, max_length=160)
    temperature: float | None = Field(None, ge=0, le=2)
    max_steps: int | None = Field(None, ge=1, le=12)
    max_output_tokens: int | None = Field(None, ge=64, le=32000)
    system_prompt: str | None = Field(None, max_length=20000)
    allowed_tools: list[str] | None = Field(None, max_length=100)
    knowledge_source_ids: list[str] | None = Field(None, max_length=100)
    memory_policy: str | None = Field(None, pattern="^(none|conversation|long_term)$")
    memory_retention_days: int | None = Field(None, ge=0, le=3650)
    monthly_budget_micros: int | None = Field(None, ge=0)
    clear_budget: bool = False
    enabled: bool | None = None
    version_note: str | None = Field(None, max_length=300)


def _validate_agent(db: Session, p: Principal, a: AiAgent) -> None:
    tools = all_tools(db, p.store.id)
    unknown = [t for t in a.allowed_tools if t not in tools]
    if unknown:
        raise AppError("unknown_tools", f"Unknown tools: {', '.join(unknown)}")
    if a.channel == "storefront":
        bad = [t for t in a.allowed_tools if tools[t].permission != PUBLIC or "storefront" not in tools[t].channels]
        if bad:
            raise AppError("tool_not_public", f"Storefront assistants can only use public read-only tools; remove "
                           f"{', '.join(bad)}")
    if a.provider_id is not None and prov.get_provider(db, p.store.id, a.provider_id) is None:
        raise AppError("unknown_provider", "Provider not found")
    ids = []
    for s in a.knowledge_source_ids:
        sid = parse_uuid(s, "Knowledge source")
        src = db.get(AiKnowledgeSource, sid)
        if src is None or src.store_id != p.store.id:
            raise AppError("unknown_source", "Knowledge source not found")
        ids.append(str(sid))
    a.knowledge_source_ids = ids
    if a.enabled and a.provider_id is None:
        raise AppError("ai_not_configured", "Assign an AI provider before enabling this assistant", status=409)


def _get_agent(db: Session, p: Principal, aid: str) -> AiAgent:
    a = db.get(AiAgent, parse_uuid(aid, "Assistant"))
    if a is None or a.store_id != p.store.id:
        raise NotFound("Assistant")
    return a


@router.get("/roles")
def roles(p: Principal = Depends(USE)):
    return {"items": [{"key": k, "label": ROLE_LABELS[k], "default_name": t["name"], "channel": t.get("channel",
                       "admin"), "tools": t["tools"], "prompt": t["prompt"]} for k, t in TEMPLATES.items()]}


@router.get("/agents")
def list_agents(db: Session = Depends(get_db), p: Principal = Depends(USE)):
    rows = db.execute(select(AiAgent).where(AiAgent.store_id == p.store.id).order_by(AiAgent.builtin.desc(),
                                                                                      AiAgent.name)).scalars().all()
    full = p.can("ai.manage")
    return {"items": [agent_out(a, full) for a in rows if full or (a.enabled and a.channel == "admin")]}


@router.get("/agents/{aid}")
def get_agent(aid: str, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    a = _get_agent(db, p, aid)
    out = agent_out(a, p.can("ai.manage"))
    out["spent_this_month_micros"] = spent_micros(db, p.store.id, a.id)
    from .runtime import effective_tool_names

    out["tools_for_you"] = effective_tool_names(db, a, Caller(p.permissions, channel=a.channel))
    return out


@router.post("/agents")
def create_agent(body: AgentIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    if body.role not in ROLE_LABELS:
        raise AppError("unknown_role", f"Unknown role template {body.role}")
    key = f"{body.role}-{uuid.uuid4().hex[:6]}"
    a = AiAgent(id=uuid.uuid4(), store_id=p.store.id, key=key, **body.model_dump(exclude={"provider_id"}),
                provider_id=parse_uuid(body.provider_id, "Provider") if body.provider_id else None)
    if not a.system_prompt:
        a.system_prompt = TEMPLATES[body.role]["prompt"]
    _validate_agent(db, p, a)
    db.add(a)
    db.add(AiAgentVersion(agent_id=a.id, version=1, system_prompt=a.system_prompt, config=agent_config(a),
                          created_by=p.actor.id, note="Created"))
    audit(db, p.store.id, p.actor, "ai.agent_created", "ai_agent", a.id, {"role": a.role, "tools": a.allowed_tools})
    db.commit()
    return agent_out(a)


@router.patch("/agents/{aid}")
def update_agent(aid: str, body: AgentPatch, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    a = _get_agent(db, p, aid)
    before = (a.system_prompt, agent_config(a))
    changes = body.model_dump(exclude_none=True, exclude={"clear_budget", "version_note", "provider_id"})
    for k, v in changes.items():
        setattr(a, k, v)
    if "provider_id" in body.model_fields_set:
        a.provider_id = parse_uuid(body.provider_id, "Provider") if body.provider_id else None
    if body.clear_budget:
        a.monthly_budget_micros = None
    _validate_agent(db, p, a)
    if (a.system_prompt, agent_config(a)) != before:
        a.version += 1
        db.add(AiAgentVersion(agent_id=a.id, version=a.version, system_prompt=a.system_prompt, config=agent_config(a),
                              created_by=p.actor.id, note=body.version_note))
    audit(db, p.store.id, p.actor, "ai.agent_updated", "ai_agent", a.id,
          {"fields": sorted(body.model_fields_set), "version": a.version})
    db.commit()
    return agent_out(a)


@router.delete("/agents/{aid}")
def delete_agent(aid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    a = _get_agent(db, p, aid)
    if a.builtin:
        raise Conflict("builtin", "Built-in assistants can be disabled but not deleted")
    db.delete(a)
    audit(db, p.store.id, p.actor, "ai.agent_deleted", "ai_agent", a.id)
    db.commit()
    return {"ok": True}


@router.get("/agents/{aid}/versions")
def versions(aid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    a = _get_agent(db, p, aid)
    rows = db.execute(select(AiAgentVersion).where(AiAgentVersion.agent_id == a.id)
                      .order_by(AiAgentVersion.version.desc())).scalars().all()
    return {"items": [{"version": v.version, "system_prompt": v.system_prompt, "config": v.config, "note": v.note,
                       "created_at": iso(v.created_at), "current": v.version == a.version} for v in rows]}


@router.post("/agents/{aid}/versions/{version}/restore")
def restore_version(aid: str, version: int, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    a = _get_agent(db, p, aid)
    v = db.execute(select(AiAgentVersion).where(AiAgentVersion.agent_id == a.id, AiAgentVersion.version == version)
                   ).scalar_one_or_none()
    if v is None:
        raise NotFound("Version")
    cfg = v.config
    a.system_prompt = v.system_prompt
    for k in ("model", "fallback_model", "temperature", "max_steps", "max_output_tokens", "allowed_tools",
              "knowledge_source_ids", "memory_policy", "memory_retention_days", "monthly_budget_micros", "channel"):
        if k in cfg:
            setattr(a, k, cfg[k])
    a.provider_id = uuid.UUID(cfg["provider_id"]) if cfg.get("provider_id") else None
    a.knowledge_source_ids = [s for s in a.knowledge_source_ids if db.get(AiKnowledgeSource, uuid.UUID(s))]
    if a.provider_id and prov.get_provider(db, p.store.id, a.provider_id) is None:
        a.provider_id, a.enabled = None, False
    a.version += 1
    db.add(AiAgentVersion(agent_id=a.id, version=a.version, system_prompt=a.system_prompt, config=agent_config(a),
                          created_by=p.actor.id, note=f"Restored from v{version}"))
    audit(db, p.store.id, p.actor, "ai.agent_restored", "ai_agent", a.id, {"from": version, "to": a.version})
    db.commit()
    return agent_out(a)


# ================================================================ chat
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


def _caller(p: Principal, channel: str = "admin") -> Caller:
    label = p.user.email if p.user is not None else (p.api_key.name if p.api_key is not None else "anonymous")
    return Caller(p.permissions, p.user.id if p.user else None, label, p.ip, channel)


@router.post("/agents/{aid}/chat")
def chat(aid: str, body: ChatIn, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    a = _get_agent(db, p, aid)
    if a.channel != "admin":
        raise AppError("wrong_channel", "This is a storefront assistant; test it from the storefront")
    if body.conversation_id:
        conv = db.get(AiConversation, parse_uuid(body.conversation_id, "Conversation"))
        if conv is None or conv.agent_id != a.id or conv.user_id != (p.user.id if p.user else None):
            raise NotFound("Conversation")
    else:
        conv = AiConversation(id=uuid.uuid4(), store_id=p.store.id, agent_id=a.id,
                              user_id=p.user.id if p.user else None, channel="admin")
        db.add(conv)
    try:
        r = run_chat(db, a, _caller(p), body.message, conversation=conv)
    except ChatFailed:
        db.commit()
        raise
    db.commit()
    return r.dump()


@store_router.get("")
def store_assistant(db: Session = Depends(get_db), p: Principal = Depends(principal)):
    a = _store_agent(db, p, required=False)
    return {"available": a is not None, "name": a.name if a else None,
            "disclosure": "You are chatting with an AI assistant. It can make mistakes; ask for a person any time."}


def _store_agent(db: Session, p: Principal, required: bool = True) -> AiAgent | None:
    a = db.execute(select(AiAgent).where(AiAgent.store_id == p.store.id, AiAgent.channel == "storefront",
                                         AiAgent.enabled, AiAgent.provider_id.is_not(None))
                   .order_by(AiAgent.created_at).limit(1)).scalar_one_or_none()
    if a is None and required:
        raise AppError("ai_not_configured", "The shopping assistant is not available on this store", status=409)
    return a


class StoreChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    conversation_token: str | None = Field(None, max_length=200)


@store_router.post("/chat")
def store_chat(body: StoreChatIn, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(principal)):
    limiter.hit(f"ai_store_ip:{p.ip}", 20, 300)
    a = _store_agent(db, p)
    token = None
    if body.conversation_id:
        conv = db.get(AiConversation, parse_uuid(body.conversation_id, "Conversation"))
        if (conv is None or conv.agent_id != a.id or not body.conversation_token
                or conv.visitor_token_hash != token_hash(body.conversation_token)):
            raise NotFound("Conversation")
    else:
        token = new_token(24)
        conv = AiConversation(id=uuid.uuid4(), store_id=p.store.id, agent_id=a.id,
                              user_id=p.user.id if p.user and p.user.kind == "customer" else None,
                              visitor_token_hash=token_hash(token), channel="storefront")
        db.add(conv)
    # Shoppers never act with staff permissions, even if a staff member is signed in.
    caller = Caller(frozenset(), conv.user_id, "shopper", p.ip, "storefront")
    try:
        r = run_chat(db, a, caller, body.message, conversation=conv)
    except ChatFailed:
        db.commit()
        raise
    db.commit()
    out = r.dump()
    out.pop("usage", None)
    out["conversation_token"] = token
    out["assistant"] = a.name
    return out


# ================================================================ tools, conversations, runs
@router.get("/tools")
def list_tools(db: Session = Depends(get_db), p: Principal = Depends(USE)):
    items = []
    for t in all_tools(db, p.store.id).values():
        d = t.dump()
        d["allowed_for_you"] = t.permission == PUBLIC or p.can(t.permission)
        d["approval"] = {"read": "none", "write": "ai.approve + " + t.permission,
                         "sensitive": "owner only"}[t.side_effect]
        items.append(d)
    return {"items": sorted(items, key=lambda x: (x["source"] != "core", x["name"]))}


@router.get("/conversations")
def conversations(agent_id: str | None = None, mine: bool = False, page: int = Query(1, ge=1),
                  db: Session = Depends(get_db), p: Principal = Depends(USE)):
    q = select(AiConversation, AiAgent.name).join(AiAgent, AiAgent.id == AiConversation.agent_id).where(
        AiConversation.store_id == p.store.id)
    if mine or not p.can("ai.manage"):
        q = q.where(AiConversation.user_id == (p.user.id if p.user else None))
    if agent_id:
        q = q.where(AiConversation.agent_id == parse_uuid(agent_id, "Assistant"))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(AiConversation.last_message_at.desc()).offset((page - 1) * 30).limit(30)).all()
    return {"items": [{"id": str(c.id), "agent_id": str(c.agent_id), "agent": name, "title": c.title,
                       "channel": c.channel, "user_id": str(c.user_id) if c.user_id else None,
                       "last_message_at": iso(c.last_message_at)} for c, name in rows],
            "total": total, "page": page, "page_size": 30}


def _get_conv(db: Session, p: Principal, cid: str) -> AiConversation:
    c = db.get(AiConversation, parse_uuid(cid, "Conversation"))
    if c is None or c.store_id != p.store.id:
        raise NotFound("Conversation")
    if not p.can("ai.manage") and c.user_id != (p.user.id if p.user else None):
        raise NotFound("Conversation")
    return c


@router.get("/conversations/{cid}")
def conversation(cid: str, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    c = _get_conv(db, p, cid)
    msgs = db.execute(select(AiMessage).where(AiMessage.conversation_id == c.id).order_by(AiMessage.created_at)
                      ).scalars().all()
    return {"id": str(c.id), "agent_id": str(c.agent_id), "title": c.title, "channel": c.channel,
            "messages": [{"id": str(m.id), "role": m.role, "content": m.content, "citations": m.citations,
                          "run_id": str(m.run_id) if m.run_id else None, "created_at": iso(m.created_at)}
                         for m in msgs]}


@router.delete("/conversations/{cid}")
def delete_conversation(cid: str, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    c = _get_conv(db, p, cid)
    db.delete(c)
    audit(db, p.store.id, p.actor, "ai.conversation_deleted", "ai_conversation", c.id)
    db.commit()
    return {"ok": True}


def run_out(r: AiRun) -> dict:
    return {"id": str(r.id), "agent_id": str(r.agent_id) if r.agent_id else None, "kind": r.kind,
            "status": r.status, "model": r.model, "steps": r.steps, "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens, "cost_micros": r.cost_micros, "latency_ms": r.latency_ms,
            "error": r.error, "feedback": r.feedback, "conversation_id": str(r.conversation_id)
            if r.conversation_id else None, "created_at": iso(r.created_at)}


@router.get("/runs")
def runs(agent_id: str | None = None, status: str | None = None, kind: str | None = None,
         page: int = Query(1, ge=1), db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    q = select(AiRun).where(AiRun.store_id == p.store.id)
    if agent_id:
        q = q.where(AiRun.agent_id == parse_uuid(agent_id, "Assistant"))
    if status:
        q = q.where(AiRun.status == status)
    if kind:
        q = q.where(AiRun.kind == kind)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(AiRun.created_at.desc()).offset((page - 1) * 50).limit(50)).scalars()
    return {"items": [run_out(r) for r in rows], "total": total, "page": page, "page_size": 50}


@router.get("/runs/{rid}")
def run_detail(rid: str, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    r = db.get(AiRun, parse_uuid(rid, "Run"))
    if r is None or r.store_id != p.store.id or (not p.can("ai.manage") and r.user_id != (p.user.id if p.user
                                                                                            else None)):
        raise NotFound("Run")
    calls = db.execute(select(AiToolCall).where(AiToolCall.run_id == r.id).order_by(AiToolCall.created_at)
                       ).scalars().all()
    return {**run_out(r), "model_calls": r.model_calls, "feedback_note": r.feedback_note,
            "tool_calls": [{"id": str(c.id), "tool": c.tool, "arguments": c.arguments, "status": c.status,
                            "result": c.result, "latency_ms": c.latency_ms,
                            "action_request_id": str(c.action_request_id) if c.action_request_id else None}
                           for c in calls]}


class FeedbackIn(BaseModel):
    rating: int = Field(ge=-1, le=1)
    note: str | None = Field(None, max_length=1000)


@router.post("/runs/{rid}/feedback")
def feedback(rid: str, body: FeedbackIn, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    r = db.get(AiRun, parse_uuid(rid, "Run"))
    if r is None or r.store_id != p.store.id or r.user_id != (p.user.id if p.user else None):
        raise NotFound("Run")
    r.feedback, r.feedback_note = body.rating or None, body.note
    db.commit()
    return {"ok": True}


# ================================================================ approvals
def req_out(r: AiActionRequest, agent_name: str | None = None) -> dict:
    return {"id": str(r.id), "agent_id": str(r.agent_id) if r.agent_id else None, "agent": agent_name,
            "tool": r.tool, "side_effect": r.side_effect, "permission": r.permission, "summary": r.summary,
            "arguments": r.arguments, "preview": r.preview, "status": r.status,
            "requested_by": str(r.requested_by) if r.requested_by else None,
            "decided_by": str(r.decided_by) if r.decided_by else None, "decided_at": iso(r.decided_at),
            "decision_note": r.decision_note, "result": r.result, "error": r.error, "created_at": iso(r.created_at),
            "expires_at": iso(r.expires_at), "run_id": str(r.run_id) if r.run_id else None}


@router.get("/approvals")
def approvals(status: str | None = "pending", page: int = Query(1, ge=1), db: Session = Depends(get_db),
              p: Principal = Depends(USE)):
    q = select(AiActionRequest, AiAgent.name).outerjoin(AiAgent, AiAgent.id == AiActionRequest.agent_id).where(
        AiActionRequest.store_id == p.store.id)
    if not (p.can("ai.approve") or p.can("ai.manage")):
        q = q.where(AiActionRequest.requested_by == (p.user.id if p.user else None))
    if status:
        q = q.where(AiActionRequest.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(AiActionRequest.created_at.desc()).offset((page - 1) * 50).limit(50)).all()
    tools = all_tools(db, p.store.id)
    items = []
    for r, name in rows:
        d = req_out(r, name)
        t = tools.get(r.tool)
        from .tools import approver_can

        d["can_approve"], d["why_not"] = approver_can(p.permissions, t) if t else (False, "Tool no longer installed")
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": 50}


class DecisionIn(BaseModel):
    note: str | None = Field(None, max_length=500)


@router.post("/approvals/{req_id}/approve")
def approve(req_id: str, body: DecisionIn, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    r = decide(db, p.store.id, parse_uuid(req_id, "Action request"), p.permissions, p.actor, True, body.note)
    return req_out(r)


@router.post("/approvals/{req_id}/reject")
def reject(req_id: str, body: DecisionIn, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    r = decide(db, p.store.id, parse_uuid(req_id, "Action request"), p.permissions, p.actor, False, body.note)
    return req_out(r)


# ================================================================ usage
@router.get("/usage")
def usage(db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    from ...core.services import get_setting
    from .runtime import STORE_BUDGET

    since = month_start()
    by = db.execute(select(AiRun.agent_id, AiRun.model, func.count(), func.sum(AiRun.prompt_tokens),
                           func.sum(AiRun.completion_tokens), func.sum(AiRun.cost_micros),
                           func.count().filter(AiRun.cost_micros.is_(None)),
                           func.count().filter(AiRun.status == "error"), func.avg(AiRun.latency_ms))
                    .where(AiRun.store_id == p.store.id, AiRun.created_at >= since)
                    .group_by(AiRun.agent_id, AiRun.model)).all()
    names = dict(db.execute(select(AiAgent.id, AiAgent.name).where(AiAgent.store_id == p.store.id)).all())
    budgets = dict(db.execute(select(AiAgent.id, AiAgent.monthly_budget_micros).where(
        AiAgent.store_id == p.store.id, AiAgent.monthly_budget_micros.is_not(None))).all())
    rows = [{"agent_id": str(a) if a else None, "agent": names.get(a), "model": m, "runs": n,
             "prompt_tokens": int(pt or 0), "completion_tokens": int(ct or 0), "cost_micros": int(c or 0),
             "unpriced_runs": up, "errors": err, "avg_latency_ms": int(lat or 0)}
            for a, m, n, pt, ct, c, up, err, lat in by]
    return {"since": iso(since), "items": rows,
            "total_cost_micros": sum(r["cost_micros"] for r in rows),
            "store_budget_micros": int(get_setting(db, p.store.id, STORE_BUDGET.key) or 0),
            "agent_budgets": [{"agent_id": str(k), "agent": names.get(k), "budget_micros": v,
                               "spent_micros": spent_micros(db, p.store.id, k)} for k, v in budgets.items()],
            "pending_approvals": db.execute(select(func.count()).where(AiActionRequest.store_id == p.store.id,
                                                                        AiActionRequest.status == "pending")).scalar()}


# ================================================================ knowledge
def src_out(s: AiKnowledgeSource) -> dict:
    return {"id": str(s.id), "name": s.name, "kind": s.kind, "url": s.url, "status": s.status, "error": s.error,
            "chunk_count": s.chunk_count, "char_count": s.char_count, "refreshed_at": iso(s.refreshed_at),
            "created_at": iso(s.created_at)}


class SourceIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    kind: str = Field(pattern="^(text|url)$")
    content: str | None = Field(None, max_length=kn.MAX_SOURCE_CHARS)
    url: str | None = Field(None, max_length=800)


@router.get("/knowledge")
def list_sources(db: Session = Depends(get_db), p: Principal = Depends(USE)):
    rows = db.execute(select(AiKnowledgeSource).where(AiKnowledgeSource.store_id == p.store.id)
                      .order_by(AiKnowledgeSource.created_at.desc())).scalars().all()
    return {"items": [src_out(s) for s in rows]}


@router.post("/knowledge")
def create_source(body: SourceIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    s = AiKnowledgeSource(id=uuid.uuid4(), store_id=p.store.id, name=body.name, kind=body.kind)
    if body.kind == "text":
        kn.ingest(db, s, body.content or "")
    else:
        if not body.url:
            raise AppError("url_required", "Enter the page URL")
        s.url = body.url
        kn.ingest(db, s, kn.fetch_url(body.url))
    db.add(s)
    audit(db, p.store.id, p.actor, "ai.knowledge_added", "ai_knowledge", s.id, {"kind": s.kind, "url": s.url})
    db.commit()
    return src_out(s)


@router.post("/knowledge/upload")
async def upload_source(file: UploadFile = File(...), db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    name = (file.filename or "upload").rsplit("/", 1)[-1][:180]
    if not name.lower().endswith((".md", ".txt", ".markdown")):
        raise AppError("unsupported_file", "Upload a .md or .txt file")
    raw = await file.read(kn.MAX_SOURCE_CHARS * 4 + 1)
    if len(raw) > kn.MAX_SOURCE_CHARS * 4:
        raise AppError("too_large", "File is too large")
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError("bad_encoding", "File must be UTF-8 text") from exc
    s = AiKnowledgeSource(id=uuid.uuid4(), store_id=p.store.id, name=name, kind="file")
    kn.ingest(db, s, body)
    db.add(s)
    audit(db, p.store.id, p.actor, "ai.knowledge_added", "ai_knowledge", s.id, {"kind": "file", "name": name})
    db.commit()
    return src_out(s)


@router.post("/knowledge/docs")
def ingest_docs(db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    rows = kn.ingest_docs(db, p.store.id)
    audit(db, p.store.id, p.actor, "ai.knowledge_docs_ingested", "ai_knowledge", None, {"files": len(rows)})
    db.commit()
    return {"items": [src_out(s) for s in rows]}


@router.post("/knowledge/{sid}/refresh")
def refresh_source(sid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    s = db.get(AiKnowledgeSource, parse_uuid(sid, "Knowledge source"))
    if s is None or s.store_id != p.store.id:
        raise NotFound("Knowledge source")
    if s.kind != "url" or not s.url:
        raise AppError("not_refreshable", "Only URL sources can be refreshed; re-upload files instead")
    try:
        kn.ingest(db, s, kn.fetch_url(s.url))
    except AppError as exc:
        s.status, s.error = "error", exc.message
    db.commit()
    return src_out(s)


@router.get("/knowledge/search")
def search_knowledge(q: str = Query(min_length=2, max_length=300), db: Session = Depends(get_db),
                     p: Principal = Depends(USE)):
    return {"items": kn.search(db, p.store.id, q, None, limit=10)}


@router.get("/knowledge/{sid}")
def source_detail(sid: str, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    s = db.get(AiKnowledgeSource, parse_uuid(sid, "Knowledge source"))
    if s is None or s.store_id != p.store.id:
        raise NotFound("Knowledge source")
    chunks = db.execute(select(AiKnowledgeChunk).where(AiKnowledgeChunk.source_id == s.id)
                        .order_by(AiKnowledgeChunk.ord).limit(50)).scalars().all()
    return {**src_out(s), "chunks": [{"ord": c.ord, "heading": c.heading, "content": c.content[:600]}
                                     for c in chunks]}


@router.delete("/knowledge/{sid}")
def delete_source(sid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    s = db.get(AiKnowledgeSource, parse_uuid(sid, "Knowledge source"))
    if s is None or s.store_id != p.store.id:
        raise NotFound("Knowledge source")
    for a in db.execute(select(AiAgent).where(AiAgent.store_id == p.store.id)).scalars():
        if str(s.id) in (a.knowledge_source_ids or []):
            a.knowledge_source_ids = [x for x in a.knowledge_source_ids if x != str(s.id)]
    db.delete(s)
    audit(db, p.store.id, p.actor, "ai.knowledge_deleted", "ai_knowledge", s.id)
    db.commit()
    return {"ok": True}


# ================================================================ memory
class MemoryIn(BaseModel):
    content: str = Field(min_length=2, max_length=2000)


@router.get("/agents/{aid}/memories")
def memories(aid: str, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    a = _get_agent(db, p, aid)
    q = select(AiMemory).where(AiMemory.agent_id == a.id)
    if not p.can("ai.manage"):
        q = q.where(AiMemory.user_id == (p.user.id if p.user else None))
    rows = db.execute(q.order_by(AiMemory.created_at.desc()).limit(200)).scalars().all()
    return {"policy": a.memory_policy, "retention_days": a.memory_retention_days,
            "items": [{"id": str(m.id), "content": m.content, "user_id": str(m.user_id) if m.user_id else None,
                       "created_at": iso(m.created_at), "expires_at": iso(m.expires_at)} for m in rows]}


@router.post("/agents/{aid}/memories")
def add_memory(aid: str, body: MemoryIn, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    a = _get_agent(db, p, aid)
    if a.memory_policy != "long_term":
        raise AppError("memory_disabled", f"{a.name} does not keep long-term memory (policy: {a.memory_policy})")
    m = AiMemory(store_id=p.store.id, agent_id=a.id, user_id=p.user.id if p.user else None, content=body.content,
                 created_by=p.user.id if p.user else None, expires_at=memory_expiry(a))
    db.add(m)
    db.commit()
    return {"id": str(m.id)}


@router.delete("/memories/{mid}")
def delete_memory(mid: str, db: Session = Depends(get_db), p: Principal = Depends(USE)):
    m = db.get(AiMemory, parse_uuid(mid, "Memory"))
    if m is None or m.store_id != p.store.id or (not p.can("ai.manage") and m.user_id != (p.user.id if p.user
                                                                                           else None)):
        raise NotFound("Memory")
    db.delete(m)
    audit(db, p.store.id, p.actor, "ai.memory_deleted", "ai_memory", m.id)
    db.commit()
    return {"ok": True}


# ================================================================ evals
class EvalIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    input: str = Field(min_length=1, max_length=4000)
    expected_tool: str | None = Field(None, max_length=80)
    must_contain: list[str] = Field(default_factory=list, max_length=20)
    must_not_contain: list[str] = Field(default_factory=list, max_length=20)


def _case_out(db: Session, c: AiEvalCase) -> dict:
    last = db.execute(select(AiEvalResult).where(AiEvalResult.case_id == c.id)
                      .order_by(AiEvalResult.created_at.desc()).limit(1)).scalar_one_or_none()
    return {"id": str(c.id), "name": c.name, "input": c.input, "expected_tool": c.expected_tool,
            "must_contain": c.must_contain, "must_not_contain": c.must_not_contain,
            "last_result": {"status": last.status, "details": last.details, "created_at": iso(last.created_at),
                            "agent_version": last.agent_version} if last else None}


@router.get("/agents/{aid}/evals")
def list_evals(aid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    a = _get_agent(db, p, aid)
    rows = db.execute(select(AiEvalCase).where(AiEvalCase.agent_id == a.id).order_by(AiEvalCase.created_at)
                      ).scalars().all()
    return {"items": [_case_out(db, c) for c in rows]}


@router.post("/agents/{aid}/evals")
def create_eval(aid: str, body: EvalIn, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    a = _get_agent(db, p, aid)
    if body.expected_tool and body.expected_tool not in all_tools(db, p.store.id):
        raise AppError("unknown_tool", f"Unknown tool {body.expected_tool}")
    c = AiEvalCase(store_id=p.store.id, agent_id=a.id, **body.model_dump())
    db.add(c)
    db.commit()
    return _case_out(db, c)


@router.delete("/evals/{cid}")
def delete_eval(cid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    c = db.get(AiEvalCase, parse_uuid(cid, "Eval case"))
    if c is None or c.store_id != p.store.id:
        raise NotFound("Eval case")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/agents/{aid}/evals/run")
def run_agent_evals(aid: str, db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    a = _get_agent(db, p, aid)
    results = run_evals(db, a, p.permissions, p.user.id if p.user else None, _caller(p).label)
    audit(db, p.store.id, p.actor, "ai.evals_run", "ai_agent", a.id,
          {"pass": sum(r["status"] == "pass" for r in results), "total": len(results)})
    db.commit()
    return {"items": results, "summary": {s: sum(r["status"] == s for r in results)
                                          for s in ("pass", "fail", "error", "not_run")}}


# ================================================================ drafts & tasks
@router.get("/drafts")
def drafts(db: Session = Depends(get_db), p: Principal = Depends(USE)):
    if not (p.can("marketing.read") or p.can("ai.manage")):
        raise Forbidden("Missing permission: marketing.read")
    rows = db.execute(select(AiDraft).where(AiDraft.store_id == p.store.id).order_by(AiDraft.created_at.desc())
                      .limit(100)).scalars().all()
    return {"items": [{"id": str(d.id), "kind": d.kind, "title": d.title, "content": d.content,
                       "created_at": iso(d.created_at)} for d in rows]}


@router.get("/tasks")
def tasks(db: Session = Depends(get_db), p: Principal = Depends(MANAGE)):
    rows = db.execute(select(AiTask).where(AiTask.store_id == p.store.id).order_by(AiTask.created_at.desc())
                      .limit(100)).scalars().all()
    return {"items": [{"id": str(t.id), "agent_id": str(t.agent_id), "source": t.source, "status": t.status,
                       "prompt": t.prompt[:500], "result": t.result, "run_id": str(t.run_id) if t.run_id else None,
                       "created_at": iso(t.created_at), "finished_at": iso(t.finished_at)} for t in rows]}


