import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.mixins import Timestamps, UUIDPK
from app.modules.opportunities.models import Opportunity


class Match(Base, UUIDPK, Timestamps):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("user_id", "opportunity_id", name="uq_match_user_opportunity"),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_match_score"),
        CheckConstraint("raw_score BETWEEN 0 AND 100", name="ck_match_raw_score"),
        Index("ix_matches_user_score", "user_id", "score"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("opportunities.id", ondelete="CASCADE")
    )
    score: Mapped[int] = mapped_column(Integer)
    raw_score: Mapped[int] = mapped_column(Integer)
    score_cap: Mapped[int] = mapped_column(Integer)
    eligibility_status: Mapped[str] = mapped_column(String(30))
    factors: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    gaps: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    next_steps: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    algorithm_version: Mapped[str] = mapped_column(String(20))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    opportunity: Mapped[Opportunity] = relationship(lazy="joined")