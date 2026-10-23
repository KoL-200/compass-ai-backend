import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import Timestamps, UUIDPK


class Goal(Base, UUIDPK, Timestamps):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(
            "category IS NULL OR category IN ('admission','scholarship','internship','job')",
            name="ck_goal_category",
        ),
        CheckConstraint("status IN ('active','achieved','abandoned')", name="ck_goal_status"),
        Index("ix_goals_user_status", "user_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(15))  # None = a goal that is not about one category
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="active", server_default=text("'active'"))
    notes: Mapped[str | None] = mapped_column(Text)