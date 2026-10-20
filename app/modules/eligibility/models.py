import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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


class EligibilityResult(Base, UUIDPK, Timestamps):
    __tablename__ = "eligibility_results"
    __table_args__ = (
        UniqueConstraint("user_id", "opportunity_id", name="uq_eligibility_user_opportunity"),
        CheckConstraint(
            "status IN ('eligible','likely_eligible','potentially_eligible','ineligible',"
            "'insufficient_information','needs_manual_verification')",
            name="ck_eligibility_status",
        ),
        Index("ix_eligibility_user_status", "user_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("opportunities.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    explanation: Mapped[str] = mapped_column(Text)
    ruleset_version: Mapped[int] = mapped_column(Integer)
    engine_version: Mapped[str] = mapped_column(String(20))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    opportunity: Mapped[Opportunity] = relationship(lazy="joined")