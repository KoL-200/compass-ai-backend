import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.mixins import Timestamps, UUIDPK
from app.modules.opportunities.models import Opportunity

_STATUS_LIST = (
    "('interested','preparing','ready_to_apply','submitted','under_review',"
    "'interview_assessment','offer_received','rejected','withdrawn','expired','completed')"
)


class ChecklistItem(Base, UUIDPK, Timestamps):
    __tablename__ = "application_checklist_items"

    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(200))
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApplicationEvent(Base, UUIDPK):
    """Append-only history. Rows are only ever inserted, never updated or deleted by the API."""

    __tablename__ = "application_events"
    __table_args__ = (CheckConstraint("actor IN ('user','system')", name="ck_event_actor"),)

    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(25))
    to_status: Mapped[str] = mapped_column(String(25))
    note: Mapped[str | None] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(10))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Application(Base, UUIDPK, Timestamps):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "opportunity_id", name="uq_application_user_opportunity"),
        CheckConstraint(f"status IN {_STATUS_LIST}", name="ck_application_status"),
        Index("ix_applications_user_status", "user_id", "status"),
        Index("ix_applications_status", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    # RESTRICT: an opportunity with applications can never be hard-deleted (admins archive instead).
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("opportunities.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(
        String(25), default="interested", server_default=text("'interested'")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    personal_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(String(300))
    reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    reminder_days: Mapped[list] = mapped_column(
        JSONB, default=lambda: [7, 3, 1], server_default=text("'[7,3,1]'::jsonb")
    )

    opportunity: Mapped[Opportunity] = relationship(lazy="joined")
    events: Mapped[list[ApplicationEvent]] = relationship(
        cascade="all, delete-orphan", order_by="ApplicationEvent.occurred_at", lazy="select"
    )
    checklist: Mapped[list[ChecklistItem]] = relationship(
        cascade="all, delete-orphan", order_by="ChecklistItem.created_at", lazy="selectin"
    )

    @property
    def effective_deadline_at(self) -> datetime | None:
        return self.personal_deadline_at or self.opportunity.deadline_at