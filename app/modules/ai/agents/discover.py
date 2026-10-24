"""Agent 2: Opportunity Hunter. Chooses which already-retrieved opportunities to look at first."""
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.ai import prompts
from app.modules.ai.agents.explain import BANNED
from app.modules.ai.orchestrator import Agent, GroundingError

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]

STATUS_PHRASE = {
    "eligible": "You appear to meet the documented requirements",
    "likely_eligible": "You appear to meet the documented requirements (confirm the details with the provider)",
    "potentially_eligible": "Some requirements could not be checked yet",
    "needs_manual_verification": "Some requirements need manual verification",
    "insufficient_information": "More profile information is needed to check eligibility",
}


class Pick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: uuid.UUID
    reason: Reason


class DiscoverProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overview: str = Field(min_length=1, max_length=400)
    picks: list[Pick] = Field(max_length=10)


def ground(proposal: DiscoverProposal, facts: dict) -> dict:
    allowed = {c["opportunity_id"] for c in facts["candidates"]}
    ids = [str(p.opportunity_id) for p in proposal.picks]
    if any(i not in allowed for i in ids):
        raise GroundingError("every opportunity_id must be copied from the candidate list")
    if len(set(ids)) != len(ids):
        raise GroundingError("each opportunity may appear only once")
    low, high = (1 if allowed else 0), min(facts["limit"], len(allowed))
    if not low <= len(ids) <= high:
        raise GroundingError(f"return between {low} and {high} picks")
    text = " ".join([proposal.overview, *(p.reason for p in proposal.picks)])
    if BANNED.search(text):
        raise GroundingError("do not state probabilities, percentages, chances or guarantees")
    return {"overview": proposal.overview,
            "picks": [{"opportunity_id": i, "reason": p.reason} for i, p in zip(ids, proposal.picks)]}


def build_rule_picks(facts: dict) -> dict:
    """Goal-aligned first, then by match score. Also the fallback when the model fails."""
    ranked = sorted(facts["candidates"], key=lambda c: (not c["goal_aligned"], -c["score"], c["title"]))
    picks = []
    for c in ranked[: facts["limit"]]:
        reason = STATUS_PHRASE.get(c["eligibility_status"], "Matches your profile") + "."
        if c["goal_aligned"]:
            reason += " It matches one of your goals."
        if c["top_factors"]:
            reason += " " + "; ".join(c["top_factors"]) + "."
        picks.append({"opportunity_id": c["opportunity_id"], "reason": reason[:300]})
    if picks:
        overview = f"Found {len(facts['candidates'])} open opportunities that fit you. Showing the top {len(picks)}."
    else:
        overview = "No open opportunities matched. Try different filters, or complete your profile."
    return {"overview": overview, "picks": picks}


DISCOVER_AGENT = Agent(
    name="opportunity_hunter", task="opportunity_discover", prompt_version=prompts.DISCOVER_VERSION,
    system=prompts.DISCOVER_SYSTEM, output_model=DiscoverProposal, build_prompt=prompts.discover_user,
    ground=ground, fallback=build_rule_picks, max_output_tokens=1500,
)