import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
TaskStatus = Literal["todo", "done", "skipped"]
StrategyStatus = Literal["draft", "accepted", "rejected", "superseded"]


def _not_in_the_past(v: date | None) -> date | None:
    if v is not None and v < date.today():
        raise ValueError("due_date cannot be in the past")
    return v


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: uuid.UUID
    force: bool = False  # ask for a plan even though a requirement is currently not met


class StrategyStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "rejected"]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    detail: str = Field(default="", max_length=300)
    due_date: date | None = None
    effort_days: int = Field(default=1, ge=1, le=30)

    _check_date = field_validator("due_date")(lambda cls, v: _not_in_the_past(v))


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    detail: str | None = Field(default=None, max_length=300)
    due_date: date | None = None
    effort_days: int | None = Field(default=None, ge=1, le=30)
    status: TaskStatus | None = None

    _check_date = field_validator("due_date")(lambda cls, v: _not_in_the_past(v))


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    title: str
    detail: str
    kind: str
    due_date: date | None
    effort_days: int
    status: str
    done_at: datetime | None
    origin: str


class OpportunityBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str
    deadline_at: datetime | None


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    opportunity: OpportunityBrief
    status: StrategyStatus
    source: str
    summary: str
    warnings: list[str]
    run_id: uuid.UUID | None
    created_at: datetime
    accepted_at: datetime | None
    tasks: list[TaskOut]


class StrategySummary(BaseModel):
    id: uuid.UUID
    opportunity: OpportunityBrief
    status: StrategyStatus
    source: str
    summary: str
    created_at: datetime
    tasks_total: int
    tasks_done: int


class StrategyPage(BaseModel):
    items: list[StrategySummary]
    total: int
    limit: int
    offset: int