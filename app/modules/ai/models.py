import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.mixins import UUIDPK

Cost = Numeric(10, 6, asdecimal=False)


class AIRunStep(Base, UUIDPK):
    __tablename__ = "ai_run_steps"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded','failed','cancelled')", name="ck_ai_step_status"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(10))  # service | agent
    agent_name: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    cost_usd: Mapped[float] = mapped_column(Cost, default=0, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(40))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIRun(Base, UUIDPK):
    __tablename__ = "ai_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')", name="ck_ai_run_status"
        ),
        Index("ix_ai_runs_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    task_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(12), default="queued", server_default=text("'queued'"))
    input_meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    output: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(String(300))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(60))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    cost_usd: Mapped[float] = mapped_column(Cost, default=0, server_default=text("0"))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    steps: Mapped[list[AIRunStep]] = relationship(
        order_by="AIRunStep.position", cascade="all, delete-orphan", lazy="selectin"
    )