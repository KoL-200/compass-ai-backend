import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ApplicationStatus = Literal[
    "interested", "preparing", "ready_to_apply", "submitted", "under_review",
    "interview_assessment", "offer_received", "rejected", "withdrawn", "expired", "completed",
]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _needs_timezone(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("must include a timezone offset, e.g. 2030-01-01T00:00:00Z")
    return v


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: uuid.UUID


class ApplicationUpdate(BaseModel):
    """Only the fields sent are changed. `note` and `submitted_at` belong to a status change."""

    model_config = ConfigDict(extra="forbid")

    status: ApplicationStatus | None = None
    note: str | None = Field(default=None, max_length=500)
    submitted_at: datetime | None = Field(
        default=None, description="When YOU submitted it. Only allowed together with status=submitted."
    )
    notes: str | None = Field(default=None, max_length=5000)
    next_action: str | None = Field(default=None, max_length=300)
    personal_deadline_at: datetime | None = None
    reminders_enabled: bool | None = None
    reminder_days: list[int] | None = Field(default=None, max_length=5)

    @field_validator("submitted_at", "personal_deadline_at")
    @classmethod
    def has_timezone(cls, v):
        return _needs_timezone(v)

    @field_validator("reminder_days")
    @classmethod
    def clean_reminder_days(cls, v):
        if v is None:
            return v
        if any(d < 0 or d > 90 for d in v):
            raise ValueError("each reminder must be between 0 and 90 days before the deadline")
        return sorted(set(v), reverse=True)

    @model_validator(mode="after")
    def note_and_date_belong_to_a_status_change(self):
        if (self.note is not None or self.submitted_at is not None) and self.status is None:
            raise ValueError("note and submitted_at can only be sent together with status")
        if self.submitted_at is not None and self.status != "submitted":
            raise ValueError("submitted_at is only allowed when status is 'submitted'")
        return self


class ChecklistCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label


class ChecklistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label | None = None
    is_done: bool | None = None


class ChecklistItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    is_done: bool
    done_at: datetime | None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_status: str | None
    to_status: str
    note: str | None
    actor: Literal["user", "system"]
    occurred_at: datetime


class OrganizationBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class ApplicationOpportunity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str
    status: str
    deadline_at: datetime | None
    application_url: str | None
    organization: OrganizationBrief


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ApplicationStatus
    opportunity: ApplicationOpportunity
    submitted_at: datetime | None  # reported by the user, not verified by us
    personal_deadline_at: datetime | None
    effective_deadline_at: datetime | None
    next_action: str | None
    notes: str | None
    reminders_enabled: bool
    reminder_days: list[int]
    created_at: datetime
    updated_at: datetime


class ApplicationDetail(ApplicationOut):
    checklist: list[ChecklistItemOut]


class ApplicationPage(BaseModel):
    items: list[ApplicationOut]
    total: int
    limit: int
    offset: int