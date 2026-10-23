import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.opportunities.schemas import Category

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
GoalStatus = Literal["active", "achieved", "abandoned"]


def _not_in_the_past(v: date | None) -> date | None:
    if v is not None and v < date.today():
        raise ValueError("target_date cannot be in the past")
    return v


class GoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    category: Category | None = None
    target_date: date | None = None
    notes: str | None = Field(default=None, max_length=1000)

    _check_date = field_validator("target_date")(lambda cls, v: _not_in_the_past(v))


class GoalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    category: Category | None = None
    target_date: date | None = None
    status: GoalStatus | None = None
    notes: str | None = Field(default=None, max_length=1000)

    _check_date = field_validator("target_date")(lambda cls, v: _not_in_the_past(v))


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str | None
    target_date: date | None
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime