"""Agent 5: Strategy. Writes the steps of a plan; software schedules them."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.ai import prompts
from app.modules.ai.agents.explain import BANNED
from app.modules.ai.orchestrator import Agent, GroundingError
from app.modules.strategy.planning import MAX_TASKS, build_rule_plan

Kind = Literal["complete_profile", "confirm_requirement", "learn_skill", "gather_documents",
               "prepare_materials", "submit", "follow_up"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Detail = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
Skill = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=100)]


class PlanTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    detail: Detail = ""
    kind: Kind
    effort_days: int = Field(ge=1, le=14)
    requirement: str | None = Field(default=None, max_length=300)
    skill: Skill | None = None


class PlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    tasks: list[PlanTask] = Field(max_length=20)


def ground(plan: PlanProposal, facts: dict) -> dict:
    if not 3 <= len(plan.tasks) <= MAX_TASKS:
        raise GroundingError(f"a plan needs between 3 and {MAX_TASKS} tasks")
    text = " ".join([plan.summary, *(f"{t.title} {t.detail}" for t in plan.tasks)])
    if BANNED.search(text):
        raise GroundingError("do not state probabilities, percentages, chances or guarantees")

    labels = {r["label"] for r in facts["requirements"]}
    missing = {s.lower() for s in facts["missing_skills"]}
    for t in plan.tasks:
        if t.kind == "learn_skill" and (t.skill or "") not in missing:
            raise GroundingError("learn_skill tasks must name a skill from missing_skills")
        if t.kind == "confirm_requirement" and t.requirement not in labels:
            raise GroundingError("confirm_requirement tasks must copy a requirement label exactly")

    kinds = [t.kind for t in plan.tasks]
    if kinds.count("submit") != 1:
        raise GroundingError("the plan must contain exactly one submit task")
    if any(k != "follow_up" for k in kinds[kinds.index("submit") + 1:]):
        raise GroundingError("only follow_up tasks may come after the submit task")
    return {"summary": plan.summary, "tasks": [t.model_dump() for t in plan.tasks]}


STRATEGY_AGENT = Agent(
    name="strategy",
    task="strategy_plan",
    prompt_version=prompts.STRATEGY_VERSION,
    system=prompts.STRATEGY_SYSTEM,
    output_model=PlanProposal,
    build_prompt=prompts.strategy_user,
    ground=ground,
    fallback=build_rule_plan,  # a plan built from the same facts, with no AI
    max_output_tokens=1800,
)