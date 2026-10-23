import uuid
from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.mixins import Timestamps, UUIDPK


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class ProfileSkill(Base):
    __tablename__ = "profile_skills"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )
    level: Mapped[str | None] = mapped_column(String(20))

    skill: Mapped[Skill] = relationship(lazy="joined")

    @property
    def name(self) -> str:
        return self.skill.name


class Qualification(Base, UUIDPK, Timestamps):
    __tablename__ = "qualifications"
    __table_args__ = (
        CheckConstraint("overall_score IS NULL OR overall_score >= 0", name="ck_qual_score_nonneg"),
        CheckConstraint(
            "overall_score IS NULL OR score_scale IS NULL OR overall_score <= score_scale",
            name="ck_qual_score_le_scale",
        ),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(50))  # e.g. WAEC, JAMB, A-Level, BSc
    subject_results: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    overall_score: Mapped[float | None] = mapped_column(Numeric(8, 2, asdecimal=False))
    score_scale: Mapped[float | None] = mapped_column(Numeric(8, 2, asdecimal=False))
    year: Mapped[int | None] = mapped_column(Integer)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class WorkExperience(Base, UUIDPK, Timestamps):
    __tablename__ = "work_experiences"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_exp_dates"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    employer: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)


class Profile(Base, UUIDPK, Timestamps):
    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    full_name: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(2))
    nationality: Mapped[str | None] = mapped_column(String(2))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    education_level: Mapped[str | None] = mapped_column(String(20))
    institution: Mapped[str | None] = mapped_column(String(200))
    field_of_study: Mapped[str | None] = mapped_column(String(200))
    graduation_year: Mapped[int | None] = mapped_column(Integer)
    needs_financial_aid: Mapped[bool | None] = mapped_column(Boolean)
    remote_preference: Mapped[str | None] = mapped_column(String(10))
    career_interests: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    headline: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    links: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    preferred_locations: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    qualifications: Mapped[list[Qualification]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="Qualification.created_at"
    )
    skills: Mapped[list[ProfileSkill]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    experiences: Mapped[list[WorkExperience]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="WorkExperience.start_date"
    )