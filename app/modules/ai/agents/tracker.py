"""Agent 7: Tracker. Summarises recorded progress. It cannot claim what the data does not show."""
import json
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.ai import prompts
from app.modules.ai.agents.explain import BANNED
from app.modules.ai.orchestrator import Agent, GroundingError

Item = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

CLAIMS = (
    (re.compile(r"\bsubmitted\b", re.I), lambda f: f["applications"]["submitted_ever"] > 0,
     "do not say anything was submitted unless submitted_ever is above zero"),
    (re.compile(r"\binterviewed\b|\binterview stage\b", re.I), lambda f: f["applications"]["interviews_ever"] > 0,
     "do not mention interviews unless interviews_ever is above zero"),
    (re.compile(r"\boffers? (received|made)\b|\breceived (an )?offers?\b|\bgot (an )?offers?\b", re.I),
     lambda f: f["applications"]["offers_ever"] > 0, "do not mention offers unless offers_ever is above zero"),
    (re.compile(r"\bcompleted\b|\bfinished\b", re.I), lambda f: f["plan_tasks"]["done"] > 0,
     "do not say anything was completed unless plan_tasks.done is above zero"),
)


class SummaryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=160)
    highlights: list[Item] = Field(default_factory=list, max_length=5)
    attention: list[Item] = Field(default_factory=list, max_length=5)


def ground(proposal: SummaryProposal, facts: dict) -> dict:
    text = " ".join([proposal.headline, *proposal.highlights, *proposal.attention])
    allowed = {int(n) for n in re.findall(r"\d+", json.dumps(facts))}
    if not {int(n) for n in re.findall(r"\d+", text)} <= allowed:
        raise GroundingError("every number must appear in the data provided")
    for pattern, permitted, message in CLAIMS:
        if pattern.search(text) and not permitted(facts):
            raise GroundingError(message)
    if BANNED.search(text):
        raise GroundingError("do not state probabilities, percentages, chances or guarantees")
    return {"headline": proposal.headline, "highlights": proposal.highlights, "attention": proposal.attention}


def build_rule_summary(facts: dict) -> dict:
    apps, tasks = facts["applications"], facts["plan_tasks"]
    if apps["total"] == 0:
        headline = "You are not tracking any applications yet."
    else:
        headline = f"You are tracking {apps['total']} application(s), {apps['active']} still active."

    highlights = []
    if apps["submitted_ever"]:
        highlights.append(f"You have recorded {apps['submitted_ever']} submission(s).")
    if apps["interviews_ever"]:
        highlights.append(f"You have reached the interview stage on {apps['interviews_ever']} application(s).")
    if apps["offers_ever"]:
        highlights.append(f"You have received {apps['offers_ever']} offer(s).")
    if tasks["total"]:
        highlights.append(f"{tasks['done']} of {tasks['total']} step(s) in your accepted plans are done.")
    if facts["status_changes_last_14_days"]:
        highlights.append(f"{facts['status_changes_last_14_days']} status change(s) recorded in the last 14 days.")

    attention = [f"Overdue by {t['days_overdue']} day(s): {t['title']}" for t in tasks["overdue"][:3]]
    attention += [f"{d['title']}: deadline in {d['days_left']} day(s)." for d in facts["upcoming_deadlines"][:3]]
    if not attention and facts["next_actions"]:
        attention.append(f"Next: {facts['next_actions'][0]['title']}")
    return {"headline": headline[:160], "highlights": [h[:200] for h in highlights[:5]],
            "attention": [a[:200] for a in attention[:5]]}


TRACKER_AGENT = Agent(
    name="tracker", task="progress_summary", prompt_version=prompts.TRACKER_VERSION,
    system=prompts.TRACKER_SYSTEM, output_model=SummaryProposal, build_prompt=prompts.tracker_user,
    ground=ground, fallback=build_rule_summary, max_output_tokens=900,
)