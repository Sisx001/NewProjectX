"""AI platform tables: providers, prices, agents (+ versions), conversations, runs/traces, approvals, knowledge,
memory, evals and background drafting tasks."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, Computed, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class AiProvider(IdMixin, TimestampMixin, Base):
    """OpenAI-compatible endpoint. The API key lives in SecretRecord `ai.provider.<id>.api_key`."""

    __tablename__ = "ai_providers"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    preset: Mapped[str] = mapped_column(String(40), default="custom")
    base_url: Mapped[str] = mapped_column(String(500))
    default_model: Mapped[str] = mapped_column(String(160), default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(String(20), default="untested")  # untested|ok|error
    models: Mapped[list[Any]] = mapped_column(default=list)  # ids reported by the last successful test
    last_test_at: Mapped[dt.datetime | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(Text)
    timeout_seconds: Mapped[int] = mapped_column(default=60)
    max_retries: Mapped[int] = mapped_column(default=2)
    requests_per_minute: Mapped[int] = mapped_column(default=60)
    supports_tools: Mapped[bool] = mapped_column(default=True)


class AiModelPrice(IdMixin, Base):
    """Owner-entered price per million tokens in micro-units of `currency` (never invented by VOLTORA)."""

    __tablename__ = "ai_model_prices"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_providers.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(160))
    input_micros_per_mtok: Mapped[int] = mapped_column(BigInteger, default=0)
    output_micros_per_mtok: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    __table_args__ = (UniqueConstraint("provider_id", "model"),)


class AiAgent(IdMixin, TimestampMixin, Base):
    __tablename__ = "ai_agents"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    channel: Mapped[str] = mapped_column(String(20), default="admin")  # admin|storefront
    enabled: Mapped[bool] = mapped_column(default=False)
    builtin: Mapped[bool] = mapped_column(default=False)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_providers.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(String(160), default="")
    fallback_model: Mapped[str] = mapped_column(String(160), default="")
    temperature: Mapped[float] = mapped_column(default=0.3)
    max_steps: Mapped[int] = mapped_column(default=5)
    max_output_tokens: Mapped[int] = mapped_column(default=1200)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(default=1)
    allowed_tools: Mapped[list[Any]] = mapped_column(default=list)
    knowledge_source_ids: Mapped[list[Any]] = mapped_column(default=list)
    memory_policy: Mapped[str] = mapped_column(String(20), default="conversation")  # none|conversation|long_term
    memory_retention_days: Mapped[int] = mapped_column(default=30)
    monthly_budget_micros: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (UniqueConstraint("store_id", "key"),)


class AiAgentVersion(IdMixin, Base):
    __tablename__ = "ai_agent_versions"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column()
    system_prompt: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    __table_args__ = (UniqueConstraint("agent_id", "version"),)


class AiConversation(IdMixin, Base):
    __tablename__ = "ai_conversations"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    visitor_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)  # anonymous storefront chats
    channel: Mapped[str] = mapped_column(String(20), default="admin")
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    last_message_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class AiMessage(IdMixin, Base):
    __tablename__ = "ai_messages"
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_conversations.id", ondelete="CASCADE"),
                                                       index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    role: Mapped[str] = mapped_column(String(20))  # user|assistant
    content: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)


class AiRun(IdMixin, Base):
    """One request/response turn including every model call and tool call (the trace)."""

    __tablename__ = "ai_runs"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_agents.id", ondelete="SET NULL"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(20), default="chat")  # chat|eval|task|test
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|error|blocked
    provider_id: Mapped[uuid.UUID | None] = mapped_column()
    model: Mapped[str] = mapped_column(String(160), default="")
    steps: Mapped[int] = mapped_column(default=0)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)  # null when the model has no owner-entered price
    latency_ms: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    model_calls: Mapped[list[Any]] = mapped_column(default=list)  # [{model, status, ms, tokens, error}]
    feedback: Mapped[int | None] = mapped_column()  # 1 | -1
    feedback_note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    __table_args__ = (Index("ix_ai_runs_store_month", "store_id", "created_at"),)


class AiToolCall(IdMixin, Base):
    __tablename__ = "ai_tool_calls"
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # ok|denied|invalid|error|unavailable|approval_required|unknown
    status: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    action_request_id: Mapped[uuid.UUID | None] = mapped_column()
    latency_ms: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class AiActionRequest(IdMixin, Base):
    """A write/sensitive tool call waiting for a human. Execution is claimed atomically (status pending→executing)."""

    __tablename__ = "ai_action_requests"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_agents.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column()
    tool: Mapped[str] = mapped_column(String(80))
    side_effect: Mapped[str] = mapped_column(String(20))
    permission: Mapped[str] = mapped_column(String(60))
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)
    preview: Mapped[dict[str, Any]] = mapped_column(default=dict)
    summary: Mapped[str] = mapped_column(String(500), default="")
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    requested_permissions: Mapped[list[Any]] = mapped_column(default=list)  # requester scope at proposal time
    # pending|executing|executed|failed|rejected|expired
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column()
    decided_at: Mapped[dt.datetime | None] = mapped_column()
    decision_note: Mapped[str | None] = mapped_column(String(500))
    result: Mapped[dict[str, Any] | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column()


class AiKnowledgeSource(IdMixin, TimestampMixin, Base):
    __tablename__ = "ai_knowledge_sources"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # text|file|url|docs
    url: Mapped[str | None] = mapped_column(String(800))
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready|error
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(default=0)
    char_count: Mapped[int] = mapped_column(default=0)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    refreshed_at: Mapped[dt.datetime | None] = mapped_column()


class AiKnowledgeChunk(IdMixin, Base):
    __tablename__ = "ai_knowledge_chunks"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_knowledge_sources.id", ondelete="CASCADE"),
                                                 index=True)
    ord: Mapped[int] = mapped_column()
    heading: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text)
    tsv = mapped_column(TSVECTOR, Computed("to_tsvector('english', coalesce(heading,'') || ' ' || content)",
                                           persisted=True))
    __table_args__ = (Index("ix_ai_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),)


class AiMemory(IdMixin, Base):
    """Long-term memory note (only for agents with memory_policy=long_term). Inspectable and deletable."""

    __tablename__ = "ai_memories"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    content: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[dt.datetime | None] = mapped_column(index=True)


class AiEvalCase(IdMixin, TimestampMixin, Base):
    __tablename__ = "ai_eval_cases"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    input: Mapped[str] = mapped_column(Text)
    expected_tool: Mapped[str | None] = mapped_column(String(80))
    must_contain: Mapped[list[Any]] = mapped_column(default=list)
    must_not_contain: Mapped[list[Any]] = mapped_column(default=list)


class AiEvalResult(IdMixin, Base):
    __tablename__ = "ai_eval_results"
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_eval_cases.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20))  # pass|fail|error|not_run
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    run_id: Mapped[uuid.UUID | None] = mapped_column()
    agent_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)


class AiDraft(IdMixin, Base):
    """Stored AI-produced draft (e.g. an email campaign). Never sent automatically."""

    __tablename__ = "ai_drafts"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # email_campaign|automation_draft
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by_agent: Mapped[uuid.UUID | None] = mapped_column()
    approved_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)


class AiTask(IdMixin, Base):
    """Background drafting request (from automations). Runs read tools; writes still become approval requests."""

    __tablename__ = "ai_tasks"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"))
    prompt: Mapped[str] = mapped_column(Text)
    permissions: Mapped[list[Any]] = mapped_column(default=list)
    source: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|done|failed|blocked
    result: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column()
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column()
