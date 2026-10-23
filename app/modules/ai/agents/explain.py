"""Agent 3: Eligibility. Explains a result that the rules engine has already decided."""
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.ai import prompts
from app.modules.ai.explain_text import build_explanation
from app.modules.ai.orchestrator import Agent, GroundingError

Item = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]

# Match scores and eligibility are not probabilities, so the model may not talk as if they were.
BANNED = re.compile(r"(\d{1,3}\s?%|\bchances?\b|\bprobabilit\w*|\blikelihood\b|\bguarantee\w*)", re.I)


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    summary: str = Field(min_length=1, max_length=600)
    strengths: list[Item] = Field(default_factory=list, max_length=10)
    gaps: list[Item] = Field(default_factory=list, max_length=10)
    unknowns: list[Item] = Field(default_factory=list, max_length=10)
    next_steps: list[Item] = Field(default_factory=list, max_length=6)


def ground(explanation: Explanation, payload: dict) -> dict:
    if explanation.status.strip().lower() != payload["status"]:
        raise GroundingError("the status does not match the stored result; repeat it exactly")
    text = " ".join([explanation.summary, *explanation.strengths, *explanation.gaps,
                     *explanation.unknowns, *explanation.next_steps])
    if BANNED.search(text):
        raise GroundingError("do not state probabilities, percentages, chances or guarantees")
    data = explanation.model_dump()
    del data["status"]  # the stored status always comes from the engine, never from the model
    return data


EXPLAIN_AGENT = Agent(
    name="eligibility",
    task="eligibility_explain",
    prompt_version=prompts.EXPLAIN_VERSION,
    system=prompts.EXPLAIN_SYSTEM,
    output_model=Explanation,
    build_prompt=prompts.explain_user,
    ground=ground,
    fallback=build_explanation,  # a plain explanation built from the engine's own data
)