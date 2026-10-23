import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


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