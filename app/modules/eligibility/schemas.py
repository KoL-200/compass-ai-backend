import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

EligibilityStatus = Literal[
    "eligible", "likely_eligible", "potentially_eligible", "ineligible",
    "insufficient_information", "needs_manual_verification",
]


class RequirementsPut(BaseModel):
    requirements: dict | None  # the key is required; null clears the rules


class OpportunityBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str


class RequirementResult(BaseModel):
    path: str
    label: str
    field: str
    op: str
    expected: Any = None
    actual: Any = None
    outcome: Literal["pass", "fail", "unknown", "review"]
    reason: str | None = None


class Counts(BaseModel):
    satisfied: int
    unsatisfied: int
    unknown: int
    review: int


class Freshness(BaseModel):
    data_quality: str
    last_verified_at: str | None
    age_days: int | None
    is_fresh: bool


class EligibilityOut(BaseModel):
    opportunity: OpportunityBrief
    status: EligibilityStatus
    explanation: str
    requirements: list[RequirementResult]
    counts: Counts
    data_freshness: Freshness
    ruleset_version: int
    engine_version: str
    evaluated_at: datetime


class EligibilityPage(BaseModel):
    items: list[EligibilityOut]
    total: int
    limit: int
    offset: int