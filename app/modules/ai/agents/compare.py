"""Agent 4: Matchmaker. Explains the trade-offs between opportunities. Software decides the best fit."""
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.ai import prompts
from app.modules.ai.agents.explain import BANNED
from app.modules.ai.orchestrator import Agent, GroundingError

Item = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
ELIGIBLE_OK = ("eligible", "likely_eligible", "potentially_eligible")


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: uuid.UUID
    strengths: list[Item] = Field(default_factory=list, max_length=5)
    trade_offs: list[Item] = Field(default_factory=list, max_length=5)


class CompareProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    comparisons: list[Comparison] = Field(max_length=4)


def choose_best(items: list[dict]) -> str | None:
    """Highest score among opportunities the person can still pursue; earlier deadline breaks ties."""
    pool = [i for i in items if i["eligibility_status"] in ELIGIBLE_OK]
    if not pool:
        return None
    best = min(pool, key=lambda i: (-i["score"], i["deadline"] or "9999-12-31", i["title"]))
    return best["opportunity_id"]


def ground(proposal: CompareProposal, facts: dict) -> dict:
    wanted = [i["opportunity_id"] for i in facts["items"]]
    ids = [str(c.opportunity_id) for c in proposal.comparisons]
    if sorted(ids) != sorted(wanted):
        raise GroundingError("return exactly one comparison for each opportunity, copying each id exactly")
    text = " ".join([proposal.summary, *(t for c in proposal.comparisons for t in (*c.strengths, *c.trade_offs))])
    if BANNED.search(text):
        raise GroundingError("do not state probabilities, percentages, chances or guarantees")
    return {"summary": proposal.summary,
            "comparisons": [{"opportunity_id": i, "strengths": c.strengths, "trade_offs": c.trade_offs}
                            for i, c in zip(ids, proposal.comparisons)]}


def build_rule_comparison(facts: dict) -> dict:
    comparisons = []
    for item in facts["items"]:
        strengths, trade_offs = [], []
        for f in item["factors"]:
            if not f["known"]:
                trade_offs.append(f"{f['label']}: not enough information yet")
            elif f["points"] * 10 >= f["max_points"] * 7:
                strengths.append(f"{f['label']}: {f['detail']}")
            elif f["points"] * 10 < f["max_points"] * 4:
                trade_offs.append(f"{f['label']}: {f['detail']}")
        trade_offs += item["gaps"][:2]
        comparisons.append({"opportunity_id": item["opportunity_id"],
                            "strengths": [s[:300] for s in strengths[:5]],
                            "trade_offs": [t[:300] for t in trade_offs[:5]]})
    if facts.get("best_fit_title"):
        summary = (f"Comparing {len(facts['items'])} opportunities. "
                   f"The highest-scoring one you can still pursue is {facts['best_fit_title']}.")
    else:
        summary = f"Comparing {len(facts['items'])} opportunities. None of them clearly fits your profile yet."
    return {"summary": summary[:600], "comparisons": comparisons}


COMPARE_AGENT = Agent(
    name="matchmaker", task="opportunity_compare", prompt_version=prompts.COMPARE_VERSION,
    system=prompts.COMPARE_SYSTEM, output_model=CompareProposal, build_prompt=prompts.compare_user,
    ground=ground, fallback=build_rule_comparison, max_output_tokens=1800,
)