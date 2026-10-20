import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2}$", to_upper=True)]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{3}$", to_upper=True)]
Url = Annotated[str, StringConstraints(pattern=r"^https?://\S+$", max_length=2000)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Tag = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=100)
]

Category = Literal["admission", "scholarship", "internship"]
WorkMode = Literal["remote", "onsite", "hybrid", "unknown"]
Status = Literal["draft", "open", "closed", "archived"]
OrgType = Literal["university", "employer", "funder", "other"]
SourceType = Literal["official_website", "official_portal", "aggregator", "manual_entry"]


def _check_tz(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("must include a timezone offset, e.g. 2030-01-01T00:00:00Z")
    return v


# ---------- organizations and sources ----------

class OrganizationCreate(BaseModel):
    name: Name
    type: OrgType
    country: CountryCode
    website: Url | None = None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    country: str
    website: str | None


class SourceCreate(BaseModel):
    name: Name
    url: Url
    type: SourceType = "manual_entry"


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    type: str
    is_official: bool
    extraction_method: str
    sync_status: str
    last_verified_at: datetime | None
    last_synced_at: datetime | None


class SourceBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    is_official: bool


# ---------- category details ----------

class _Details(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class AdmissionDetails(_Details):
    degree_type: Literal["certificate", "diploma", "bachelor", "master", "doctorate"]
    department: Name | None = None
    duration_months: int | None = Field(default=None, ge=1, le=120)
    tuition_amount: float | None = Field(default=None, ge=0)
    tuition_currency: CurrencyCode | None = None
    entry_requirements_text: str | None = Field(default=None, max_length=5000)
    application_portal_url: Url | None = None

    @model_validator(mode="after")
    def amount_needs_currency(self):
        if self.tuition_amount is not None and self.tuition_currency is None:
            raise ValueError("tuition_currency is required when tuition_amount is set")
        return self


class ScholarshipDetails(_Details):
    funding_type: Literal["full", "partial", "stipend", "other"]
    funding_amount: float | None = Field(default=None, ge=0)
    funding_currency: CurrencyCode | None = None
    coverage: list[Tag] = Field(default_factory=list, max_length=20)
    need_based: bool | None = None
    renewable: bool | None = None
    renewal_conditions: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def amount_needs_currency(self):
        if self.funding_amount is not None and self.funding_currency is None:
            raise ValueError("funding_currency is required when funding_amount is set")
        return self


class InternshipDetails(_Details):
    duration_weeks: int | None = Field(default=None, ge=1, le=104)
    start_date: date | None = None
    paid: bool | None = None
    compensation_amount: float | None = Field(default=None, ge=0)
    compensation_currency: CurrencyCode | None = None
    required_skills: list[Tag] = Field(default_factory=list, max_length=30)
    min_experience_months: int = Field(default=0, ge=0, le=600)

    @model_validator(mode="after")
    def amount_needs_currency(self):
        if self.compensation_amount is not None and self.compensation_currency is None:
            raise ValueError("compensation_currency is required when compensation_amount is set")
        return self


DETAIL_MODELS: dict[str, type[_Details]] = {
    "admission": AdmissionDetails,
    "scholarship": ScholarshipDetails,
    "internship": InternshipDetails,
}


# ---------- opportunities: create ----------

class _OpportunityCommon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Name
    description: str | None = Field(default=None, max_length=10000)
    organization_id: uuid.UUID
    source_id: uuid.UUID | None = None
    country: CountryCode | None = None
    location: str | None = Field(default=None, max_length=200)
    work_mode: WorkMode = "unknown"
    application_url: Url | None = None
    opens_at: datetime | None = None
    deadline_at: datetime | None = None
    status: Status = "draft"

    @field_validator("opens_at", "deadline_at")
    @classmethod
    def has_timezone(cls, v):
        return _check_tz(v)

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.opens_at and self.deadline_at and self.deadline_at <= self.opens_at:
            raise ValueError("deadline_at must be after opens_at")
        return self


class AdmissionOpportunityCreate(_OpportunityCommon):
    category: Literal["admission"]
    admission: AdmissionDetails


class ScholarshipOpportunityCreate(_OpportunityCommon):
    category: Literal["scholarship"]
    scholarship: ScholarshipDetails


class InternshipOpportunityCreate(_OpportunityCommon):
    category: Literal["internship"]
    internship: InternshipDetails


OpportunityCreate = Annotated[
    Union[
        AdmissionOpportunityCreate,
        ScholarshipOpportunityCreate,
        InternshipOpportunityCreate,
    ],
    Field(discriminator="category"),
]


# ---------- opportunities: update ----------

class OpportunityUpdate(BaseModel):
    """Partial update. The category can never be changed."""

    model_config = ConfigDict(extra="forbid")

    title: Name | None = None
    description: str | None = Field(default=None, max_length=10000)
    organization_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    country: CountryCode | None = None
    location: str | None = Field(default=None, max_length=200)
    work_mode: WorkMode | None = None
    application_url: Url | None = None
    opens_at: datetime | None = None
    deadline_at: datetime | None = None
    status: Status | None = None
    details: dict[str, Any] | None = None  # merged into the existing details, then re-validated

    @field_validator("opens_at", "deadline_at")
    @classmethod
    def has_timezone(cls, v):
        return _check_tz(v)


# ---------- opportunities: output ----------

class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str
    title: str
    description: str | None
    organization: OrganizationOut
    source: SourceBrief | None
    country: str | None
    location: str | None
    work_mode: str
    application_url: str | None
    opens_at: datetime | None
    deadline_at: datetime | None
    status: str
    data_quality: str
    last_verified_at: datetime | None
    requirements: dict | None
    requirements_version: int
    admission: AdmissionDetails | None
    scholarship: ScholarshipDetails | None
    internship: InternshipDetails | None
    created_at: datetime
    updated_at: datetime


class OpportunityPage(BaseModel):
    items: list[OpportunityOut]
    total: int
    limit: int
    offset: int