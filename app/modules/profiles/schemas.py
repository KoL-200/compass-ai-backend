import uuid
from datetime import date
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2}$", to_upper=True)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
EducationLevel = Literal["secondary", "diploma", "bachelor", "master", "doctorate", "other"]
RemotePreference = Literal["remote", "onsite", "hybrid", "any"]
SkillLevel = Literal["beginner", "intermediate", "advanced"]
Link = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^https?://\S+$", max_length=300)]


def _normalize_subjects(v: dict[str, str]) -> dict[str, str]:
    return {k.strip().lower(): g.strip() for k, g in v.items() if k.strip()}


class ProfileUpdate(BaseModel):
    full_name: ShortText | None = None
    country: CountryCode | None = None
    nationality: CountryCode | None = None
    date_of_birth: date | None = None
    education_level: EducationLevel | None = None
    institution: ShortText | None = None
    field_of_study: ShortText | None = None
    graduation_year: int | None = Field(default=None, ge=1950, le=2100)
    needs_financial_aid: bool | None = None
    remote_preference: RemotePreference | None = None
    career_interests: list[Tag] | None = Field(default=None, max_length=20)
    preferred_locations: list[Tag] | None = Field(default=None, max_length=20)
    headline: ShortText | None = None
    summary: str | None = Field(default=None, max_length=2000)
    links: list[Link] | None = Field(default=None, max_length=10)

    @field_validator("date_of_birth")
    @classmethod
    def not_in_future(cls, v):
        if v is not None and v > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return v


class QualificationCreate(BaseModel):
    type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    subject_results: dict[str, str] = Field(default_factory=dict, max_length=30)
    overall_score: float | None = Field(default=None, ge=0)
    score_scale: float | None = Field(default=None, gt=0)
    year: int | None = Field(default=None, ge=1950, le=2100)

    _norm = field_validator("subject_results")(lambda cls, v: _normalize_subjects(v))

    @model_validator(mode="after")
    def score_within_scale(self):
        if (
            self.overall_score is not None
            and self.score_scale is not None
            and self.overall_score > self.score_scale
        ):
            raise ValueError("overall_score cannot exceed score_scale")
        return self


class QualificationUpdate(BaseModel):
    type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)] | None = None
    subject_results: dict[str, str] | None = Field(default=None, max_length=30)
    overall_score: float | None = Field(default=None, ge=0)
    score_scale: float | None = Field(default=None, gt=0)
    year: int | None = Field(default=None, ge=1950, le=2100)

    @field_validator("subject_results")
    @classmethod
    def _norm(cls, v):
        return None if v is None else _normalize_subjects(v)


class QualificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    subject_results: dict[str, str]
    overall_score: float | None
    score_scale: float | None
    year: int | None
    verified: bool


class SkillIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    level: SkillLevel | None = None


class SkillsReplace(BaseModel):
    skills: list[SkillIn] = Field(max_length=100)


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    level: str | None


class ExperienceCreate(BaseModel):
    title: ShortText
    employer: ShortText
    start_date: date
    end_date: date | None = None
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class ExperienceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    employer: str
    start_date: date
    end_date: date | None
    description: str | None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    full_name: str | None
    country: str | None
    nationality: str | None
    date_of_birth: date | None
    education_level: str | None
    institution: str | None
    field_of_study: str | None
    graduation_year: int | None
    needs_financial_aid: bool | None
    remote_preference: str | None
    career_interests: list[str]
    preferred_locations: list[str]
    headline: str | None
    summary: str | None
    links: list[str]
    qualifications: list[QualificationOut]
    skills: list[SkillOut]
    experiences: list[ExperienceOut]
    missing_fields: list[str] = []