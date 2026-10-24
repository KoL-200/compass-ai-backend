import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.opportunities.schemas import Category


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=5000)]


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: uuid.UUID


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[str] | None = Field(default=None, max_length=20)


class RunCreated(BaseModel):
    run_id: uuid.UUID
    status: str


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    kind: str
    agent_name: str | None
    status: str
    attempts: int
    duration_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    error_code: str | None
    meta: dict[str, Any]


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_type: str
    status: str
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None


class RunOut(RunSummary):
    requires_approval: bool
    applied_at: datetime | None
    output: dict[str, Any] | None
    error: str | None
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    started_at: datetime | None
    steps: list[StepOut]


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int


class FieldChange(BaseModel):
    field: str
    before: Any = None
    after: Any = None


class ApplyOut(BaseModel):
    applied: list[str]
    changes: list[FieldChange]


class UsageOut(BaseModel):
    date: str
    runs_today: int
    daily_limit: int
    tokens_in_today: int
    tokens_out_today: int
    cost_usd_today: float
    provider: str
    model: str

class GuidanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: uuid.UUID
    force: bool = False

class DiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category | None = None
    q: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    limit: int = Field(default=5, ge=1, le=10)


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_ids: list[uuid.UUID] = Field(min_length=2, max_length=4)

    @field_validator("opportunity_ids")
    @classmethod
    def unique(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("opportunity_ids must be unique")
        return v


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=50, max_length=8000)]
    kind: Literal["personal_statement", "cv", "cover_letter", "other"] = "other"


class ProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notify: bool = False  # also post the summary as an in-app notification


class ChecklistApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Annotated[int, Field(ge=0)]] | None = Field(default=None, max_length=15)


class ChecklistApplyOut(BaseModel):
    added: list[str]
    skipped: list[str]