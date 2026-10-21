import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.modules.eligibility.schemas import EligibilityStatus

SCORE_NOTE = (
    "The score ranks relevance to your profile and goals. It is not a probability "
    "of being admitted, funded or hired."
)


class OrgBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class MatchOpportunity(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str
    organization: OrgBrief
    country: str | None
    location: str | None
    work_mode: str
    deadline_at: datetime | None
    application_url: str | None


class Factor(BaseModel):
    name: str
    label: str
    points: int
    max_points: int
    detail: str
    known: bool


class MatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    opportunity: MatchOpportunity
    score: int
    raw_score: int
    score_cap: int
    eligibility_status: EligibilityStatus
    factors: list[Factor]
    gaps: list[str]
    next_steps: list[str]
    algorithm_version: str
    calculated_at: datetime


class MatchPage(BaseModel):
    items: list[MatchOut]
    total: int
    limit: int
    offset: int
    note: str = SCORE_NOTE


class RefreshOut(BaseModel):
    computed: int
    truncated: bool
    by_status: dict[str, int]
    algorithm_version: str