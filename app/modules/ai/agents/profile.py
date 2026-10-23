"""Agent 1: Profile Intelligence. Turns free text into a proposed profile update."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.ai import knowledge as k
from app.modules.ai import prompts
from app.modules.ai.orchestrator import Agent
from app.modules.profiles.schemas import CountryCode, EducationLevel, RemotePreference, ShortText

Tag = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=100)]


class ProfileProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: CountryCode | None = None
    nationality: CountryCode | None = None
    education_level: EducationLevel | None = None
    institution: ShortText | None = None
    field_of_study: ShortText | None = None
    graduation_year: int | None = Field(default=None, ge=1950, le=2100)
    skills: list[Tag] = Field(default_factory=list, max_length=30)
    career_interests: list[Tag] = Field(default_factory=list, max_length=10)
    remote_preference: RemotePreference | None = None
    needs_financial_aid: bool | None = None


def ground(proposal: ProfileProposal, payload: dict) -> dict:
    """Keep only what the text supports. Everything else is reported as unverified and cannot be applied."""
    text = payload["text"]
    low = text.lower()
    supported: dict = {}
    unverified: dict = {}

    def sort(field: str, value, ok: bool) -> None:
        (supported if ok else unverified)[field] = value

    for field in ("country", "nationality"):
        value = getattr(proposal, field)
        if value is not None:
            sort(field, value, any(k.contains_term(text, a) for a in k.COUNTRY_ALIASES.get(value, [])))
    if proposal.education_level is not None:
        terms = k.LEVEL_KEYWORDS.get(proposal.education_level, [])
        sort("education_level", proposal.education_level, any(k.contains_term(text, t) for t in terms))
    for field in ("institution", "field_of_study"):
        value = getattr(proposal, field)
        if value is not None:
            sort(field, value, value.lower() in low)
    if proposal.graduation_year is not None:
        sort("graduation_year", proposal.graduation_year, str(proposal.graduation_year) in text)
    if proposal.remote_preference is not None:
        terms = k.REMOTE_KEYWORDS.get(proposal.remote_preference, [])
        sort("remote_preference", proposal.remote_preference, any(t in low for t in terms))
    if proposal.needs_financial_aid is not None:
        phrases = k.AID_YES if proposal.needs_financial_aid else k.AID_NO
        sort("needs_financial_aid", proposal.needs_financial_aid, any(p in low for p in phrases))
    for field in ("skills", "career_interests"):
        values = getattr(proposal, field)
        good = [v for v in values if k.contains_term(text, v)]
        bad = [v for v in values if v not in good]
        if good:
            supported[field] = good
        if bad:
            unverified[field] = bad
    return {"proposal": supported, "unverified": unverified}


PROFILE_AGENT = Agent(
    name="profile_intelligence",
    task="profile_extract",
    prompt_version=prompts.PROFILE_EXTRACT_VERSION,
    system=prompts.PROFILE_EXTRACT_SYSTEM,
    output_model=ProfileProposal,
    build_prompt=lambda payload: prompts.profile_extract_user(payload["text"]),
    ground=ground,
    fallback=None,  # there is no honest non-AI substitute for reading free text
)