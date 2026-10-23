import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.mixins import Timestamps, UUIDPK
from app.modules.opportunities.models import Opportunity
from app.modules.strategy.planning import STORED_KINDS

_KINDS = ",".join(f"'{k}'" for k in STORED_KINDS)


class StrategyTask(Base, UUIDPK, Timestamps):
    __tablename__ = "strategy_tasks"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_KINDS})", name="ck_strategy_task_kind"),
        CheckConstraint("status IN ('todo','done','skipped')", name="ck_strategy_task_status"),
        CheckConstraint("origin IN ('ai','rules','user')", name="ck_strategy_task_origin"),
        CheckConstraint("effort_days BETWEEN 1 AND 30", name="ck_strategy_task_effort"),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    kind: Mapped[str] = mapped_column(String(25))
    due_date: Mapped[date | None] = mapped_column(Date)
    effort_days: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(10), default="todo", server_default=text("'todo'"))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(10))


class Strategy(Base, UUIDPK, Timestamps):
    __tablename__ = "strategies"
    __table_args__ = (
        CheckConstraint("status IN ('draft','accepted','rejected','superseded')", name="ck_strategy_status"),
        CheckConstraint("source IN ('ai','rules')", name="ck_strategy_source"),
        Index("ix_strategies_user_opportunity", "user_id", "opportunity_id"),
        # A person can have only one accepted plan per opportunity.
        Index("uq_strategy_one_accepted", "user_id", "opportunity_id", unique=True,
              postgresql_where=text("status = 'accepted'")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("opportunities.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default=text("'draft'"))
    source: Mapped[str] = mapped_column(String(5))
    summary: Mapped[str] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ai_runs.id", ondelete="SET NULL"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    opportunity: Mapped[Opportunity] = relationship(lazy="joined")
    tasks: Mapped[list[StrategyTask]] = relationship(
        cascade="all, delete-orphan", order_by="StrategyTask.position", lazy="selectin"
    )