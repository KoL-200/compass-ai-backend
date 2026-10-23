import uuid

from pydantic import BaseModel


class OpportunityBrief(BaseModel):
    id: uuid.UUID
    title: str
    category: str


class SkillGroup(BaseModel):
    total: int
    matched: list[str]
    missing: list[str]
    unknown: list[str]


class ExperienceGap(BaseModel):
    required_months: int
    your_months: int | None
    gap_months: int | None


class SkillsGapOut(BaseModel):
    opportunity: OpportunityBrief
    skills_recorded: bool
    required: SkillGroup
    preferred: SkillGroup
    coverage_percent: int | None
    experience: ExperienceGap


class Example(BaseModel):
    id: str
    title: str


class SkillToLearn(BaseModel):
    skill: str
    required_in: int
    preferred_in: int
    would_complete: int
    examples: list[Example]


class SkillsToLearnOut(BaseModel):
    postings_considered: int
    excluded_ineligible: int
    skills_recorded: bool
    skills: list[SkillToLearn]
    note: str


class CommonSkill(BaseModel):
    skill: str
    postings: int
    required_by_percent: int


class SalarySummary(BaseModel):
    currency: str
    period: str
    lowest: float
    highest: float
    postings: int


class LevelOut(BaseModel):
    level: str
    postings: int
    typical_min_experience_months: int
    common_skills: list[CommonSkill]
    salary: list[SalarySummary]


class NextStep(BaseModel):
    level: str
    skills_recorded: bool
    already_have: list[str]
    to_learn: list[str]
    unknown: list[str]


class CareerPathOut(BaseModel):
    role_family: str
    postings_analyzed: int
    estimated_current_level: str | None
    levels: list[LevelOut]
    next_step: NextStep | None
    note: str


class RoleFamilyOut(BaseModel):
    role_family: str
    postings: int