import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class MissingField(BaseModel):
    field: str
    label: str
    helps: list[str]


class ProfileSummary(BaseModel):
    completeness_percent: int
    missing: list[MissingField]
    email_verified: bool


class GoalBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str | None
    target_date: date | None
    status: str


class UpcomingDeadline(BaseModel):
    application_id: uuid.UUID
    title: str
    category: str
    status: str
    deadline_at: datetime
    days_left: int


class ApplicationSummary(BaseModel):
    total: int
    active: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    upcoming_deadlines: list[UpcomingDeadline]


class MatchBrief(BaseModel):
    opportunity_id: uuid.UUID
    title: str
    category: str
    organization: str
    score: int
    eligibility_status: str
    deadline_at: datetime | None
    goal_aligned: bool
    tracked: bool


class MatchSummary(BaseModel):
    last_computed_at: datetime | None
    visible_total: int
    top_by_category: dict[str, list[MatchBrief]]


class Progress(BaseModel):
    matched: int
    tracked: int
    submitted: int
    interviews: int
    offers: int
    note: str


class ActionTarget(BaseModel):
    kind: str
    id: str


class NextAction(BaseModel):
    type: str
    priority: int
    title: str
    detail: str
    target: ActionTarget | None
    due_at: datetime | None


class DashboardOut(BaseModel):
    profile: ProfileSummary
    goals: list[GoalBrief]
    applications: ApplicationSummary
    matches: MatchSummary
    progress: Progress
    unread_notifications: int
    next_actions: list[NextAction]