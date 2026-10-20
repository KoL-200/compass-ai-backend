import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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

Money = Numeric(12, 2, asdecimal=False)


class Organization(Base, UUIDPK, Timestamps):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("name", "country", name="uq_org_name_country"),
        CheckConstraint("type IN ('university','employer','funder','other')", name="ck_org_type"),
    )

    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(2))
    website: Mapped[str | None] = mapped_column(String(500))


class Source(Base, UUIDPK, Timestamps):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "type IN ('official_website','official_portal','aggregator','manual_entry')",
            name="ck_source_type",
        ),
    )

    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    type: Mapped[str] = mapped_column(String(30), default="manual_entry")
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_method: Mapped[str] = mapped_column(String(30), default="manual")
    sync_status: Mapped[str] = mapped_column(String(20), default="manual")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


# ---- category detail tables (1:1 with opportunities) ----

class AdmissionDetails(Base):
    __tablename__ = "admission_details"
    __table_args__ = (
        CheckConstraint(
            "degree_type IN ('certificate','diploma','bachelor','master','doctorate')",
            name="ck_admission_degree",
        ),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True
    )
    degree_type: Mapped[str] = mapped_column(String(20))
    department: Mapped[str | None] = mapped_column(String(200))
    duration_months: Mapped[int | None] = mapped_column(Integer)
    tuition_amount: Mapped[float | None] = mapped_column(Money)
    tuition_currency: Mapped[str | None] = mapped_column(String(3))
    entry_requirements_text: Mapped[str | None] = mapped_column(Text)
    application_portal_url: Mapped[str | None] = mapped_column(String(2000))


class ScholarshipDetails(Base):
    __tablename__ = "scholarship_details"
    __table_args__ = (
        CheckConstraint(
            "funding_type IN ('full','partial','stipend','other')", name="ck_scholarship_funding"
        ),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True
    )
    funding_type: Mapped[str] = mapped_column(String(10))
    funding_amount: Mapped[float | None] = mapped_column(Money)
    funding_currency: Mapped[str | None] = mapped_column(String(3))
    coverage: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    need_based: Mapped[bool | None] = mapped_column(Boolean)
    renewable: Mapped[bool | None] = mapped_column(Boolean)
    renewal_conditions: Mapped[str | None] = mapped_column(Text)


class InternshipDetails(Base):
    __tablename__ = "internship_details"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True
    )
    duration_weeks: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date | None] = mapped_column(Date)
    paid: Mapped[bool | None] = mapped_column(Boolean)
    compensation_amount: Mapped[float | None] = mapped_column(Money)
    compensation_currency: Mapped[str | None] = mapped_column(String(3))
    required_skills: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    min_experience_months: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )


# ---- the shared opportunity table ----

class Opportunity(Base, UUIDPK, Timestamps):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint(
            "category IN ('admission','scholarship','internship')", name="ck_opp_category"
        ),
        CheckConstraint("status IN ('draft','open','closed','archived')", name="ck_opp_status"),
        CheckConstraint(
            "work_mode IN ('remote','onsite','hybrid','unknown')", name="ck_opp_work_mode"
        ),
        CheckConstraint(
            "data_quality IN ('unverified','verified','needs_review')", name="ck_opp_quality"
        ),
        CheckConstraint(
            "opens_at IS NULL OR deadline_at IS NULL OR deadline_at > opens_at", name="ck_opp_dates"
        ),
        Index("ix_opportunities_listing", "category", "status", "deadline_at"),
    )

    category: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    country: Mapped[str | None] = mapped_column(String(2))
    location: Mapped[str | None] = mapped_column(String(200))
    work_mode: Mapped[str] = mapped_column(
        String(10), default="unknown", server_default=text("'unknown'")
    )
    application_url: Mapped[str | None] = mapped_column(String(2000))
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default=text("'draft'"))
    data_quality: Mapped[str] = mapped_column(
        String(15), default="unverified", server_default=text("'unverified'")
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requirements: Mapped[dict | None] = mapped_column(JSONB)  # rule tree, filled in Part F
    requirements_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )

    organization: Mapped[Organization] = relationship(lazy="joined")
    source: Mapped[Source | None] = relationship(lazy="joined")
    admission: Mapped[AdmissionDetails | None] = relationship(
        cascade="all, delete-orphan", lazy="selectin", uselist=False
    )
    scholarship: Mapped[ScholarshipDetails | None] = relationship(
        cascade="all, delete-orphan", lazy="selectin", uselist=False
    )
    internship: Mapped[InternshipDetails | None] = relationship(
        cascade="all, delete-orphan", lazy="selectin", uselist=False
    )