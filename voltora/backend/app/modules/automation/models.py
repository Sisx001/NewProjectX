from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base, IdMixin, TimestampMixin, utcnow


class Workflow(IdMixin, TimestampMixin, Base):
    __tablename__ = "automation_workflows"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    trigger_event: Mapped[str] = mapped_column(String(80), index=True)
    conditions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    actions: Mapped[list[Any]] = mapped_column(default=list)
    enabled: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    max_runs_per_hour: Mapped[int] = mapped_column(default=500)
    # Permission snapshot of the editor at save time; AI drafting actions never exceed it.
    run_as_permissions: Mapped[list[Any]] = mapped_column(default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    updated_by: Mapped[uuid.UUID | None] = mapped_column()
    last_run_at: Mapped[dt.datetime | None] = mapped_column()


class WorkflowRun(IdMixin, Base):
    """One execution per (workflow, outbox event). Steps record per-action results so retries resume safely."""

    __tablename__ = "automation_runs"
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("automation_workflows.id", ondelete="CASCADE"),
                                                   index=True)
    workflow_version: Mapped[int] = mapped_column(default=1)
    event_id: Mapped[uuid.UUID | None] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # running|succeeded|failed|dead|skipped|throttled|test
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    conditions_met: Mapped[bool | None] = mapped_column()
    steps: Mapped[list[Any]] = mapped_column(default=list)
    attempts: Mapped[int] = mapped_column(default=1)
    error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[dt.datetime | None] = mapped_column(index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column()
    __table_args__ = (UniqueConstraint("workflow_id", "event_id"),)
