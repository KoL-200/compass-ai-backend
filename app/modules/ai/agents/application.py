"""Agent 6: Application. Proposes checklist items and reviews drafts. It never writes application content."""
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.ai import prompts
from app.modules.ai.agents.explain import BANNED
from app.modules.ai.orchestrator import Agent, GroundingError

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Quote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=100)]

# ---------- checklist proposals ----------

DOCS_FOR_FIELD = {
    "qualification_score": "Official result for your {qualification_type} score",
    "subject_grade": "Certificate or result slip showing your {subject} grade",
    "qualification_types": "Certificates for your qualifications",
    "subjects": "Result slips showing your subjects",
    "education_level": "Certificate for your highest completed education",
    "graduation_year": "Graduation certificate",
    "field_of_study": "Evidence of your field of study (certificate or transcript)",
    "date_of_birth": "Proof of date of birth",
    "age": "Proof of date of birth",
    "country": "Proof of residence",
    "nationality": "Proof of nationality (ID or passport)",
    "experience_months": "CV or employer letters showing your work experience",
    "skills": "Evidence of your skills (portfolio, projects or certificates)",
    "needs_financial_aid": "Supporting documents for your financial situation, if the provider asks for them",
}
COMMON = "Common for this kind of application. Confirm on the official page."
_WORK = [("Up-to-date CV", COMMON), ("Cover letter or personal statement, if requested", COMMON),
         ("References, if requested", COMMON)]
STANDARD = {
    "admission": [("Application form completed on the official portal", COMMON),
                  ("Official transcripts or result slips", COMMON), ("Valid ID or passport", COMMON)],
    "scholarship": [("Scholarship application form", COMMON),
                    ("Proof of enrolment or admission, if the award asks for it", COMMON),
                    ("Recommendation letters, if the award asks for them", COMMON)],
    "internship": _WORK,
    "job": _WORK,
}


class ChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label
    reason: str = Field(default="", max_length=300)
    source: Literal["requirement", "standard"]
    requirement: str | None = Field(default=None, max_length=300)


class ChecklistProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ChecklistItem] = Field(max_length=15)


def ground_checklist(proposal: ChecklistProposal, facts: dict) -> dict:
    if not 1 <= len(proposal.items) <= facts["limit"]:
        raise GroundingError(f"return between 1 and {facts['limit']} items")
    seen = {label.casefold() for label in facts["existing_items"]}
    allowed = {r["label"] for r in facts["requirements"]}
    for item in proposal.items:
        key = item.label.casefold()
        if key in seen:
            raise GroundingError(f"'{item.label}' repeats an existing or earlier item")
        seen.add(key)
        if item.source == "requirement" and item.requirement not in allowed:
            raise GroundingError("requirement items must copy a requirement label exactly")
        if item.source == "standard" and item.requirement is not None:
            raise GroundingError("standard items must have requirement set to null")
    if BANNED.search(" ".join(f"{i.label} {i.reason}" for i in proposal.items)):
        raise GroundingError("do not state probabilities, percentages, chances or guarantees")
    return {"items": [i.model_dump() for i in proposal.items]}


def _doc_for(req: dict) -> str | None:
    template = DOCS_FOR_FIELD.get(req["field"])
    if template is None:
        return None
    return template.format(
        qualification_type=(req.get("qualification_type") or "qualification").upper(),
        subject=req.get("subject") or "subject",
    )


def build_rule_checklist(facts: dict) -> dict:
    taken = {label.casefold() for label in facts["existing_items"]}
    items: list[dict] = []

    def add(label, reason, source, requirement=None):
        if label.casefold() in taken or len(items) >= facts["limit"]:
            return
        taken.add(label.casefold())
        items.append({"label": label[:120], "reason": reason[:300], "source": source,
                      "requirement": requirement})

    for req in facts["requirements"]:
        doc = _doc_for(req)
        if doc:
            add(doc, f"Supports the requirement: {req['label']}", "requirement", req["label"])
    for label, reason in STANDARD.get(facts["opportunity"]["category"], []):
        add(label, reason, "standard")
    return {"items": items}


CHECKLIST_AGENT = Agent(
    name="application", task="application_checklist", prompt_version=prompts.CHECKLIST_VERSION,
    system=prompts.CHECKLIST_SYSTEM, output_model=ChecklistProposal, build_prompt=prompts.checklist_user,
    ground=ground_checklist, fallback=build_rule_checklist, max_output_tokens=1500,
)

# ---------- draft review ----------


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: str = Field(min_length=1, max_length=300)
    quote: Quote | None = None


class ReviewProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    observations: list[Observation] = Field(max_length=8)
    missing_topics: list[Topic] = Field(default_factory=list, max_length=8)


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _numbers(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"\d+", text)}


def ground_review(proposal: ReviewProposal, facts: dict) -> dict:
    draft = _norm(facts["text"])
    for obs in proposal.observations:
        if obs.quote is not None and _norm(obs.quote) not in draft:
            raise GroundingError("a quote must be copied exactly from the draft")
    allowed_topics = {s.lower() for s in facts["skills"]["not_mentioned"]}
    if any(t not in allowed_topics for t in proposal.missing_topics):
        raise GroundingError("missing_topics may only contain items from skills.not_mentioned")
    text = " ".join([proposal.summary, *(o.point for o in proposal.observations)])
    allowed_numbers = _numbers(facts["text"]) | _numbers(json.dumps({k: v for k, v in facts.items() if k != "text"}))
    if not _numbers(text) <= allowed_numbers:
        raise GroundingError("every number must come from the draft or the data provided")
    if BANNED.search(text):
        raise GroundingError("do not state probabilities, percentages, chances or guarantees")
    return {"summary": proposal.summary,
            "observations": [{"point": o.point, "quote": o.quote} for o in proposal.observations],
            "missing_topics": list(proposal.missing_topics)}


def build_rule_review(facts: dict) -> dict:
    missing = facts["skills"]["not_mentioned"]
    observations = [
        {"point": f"The posting asks for {skill}, which your draft does not mention. "
                  "Add it only if it is true for you.", "quote": None}
        for skill in missing[:6]
    ]
    stats = facts["stats"]
    observations.append({"point": f"Your draft is {stats['words']} words in {stats['paragraphs']} paragraph(s).",
                         "quote": None})
    wanted = facts["skills"]["required"] + facts["skills"]["preferred"]
    if missing:
        summary = f"Your draft does not mention {facts['skills']['not_mentioned_count']} skill(s) the posting asks for."
    elif wanted:
        summary = "Your draft mentions every skill the posting asks for."
    else:
        summary = "Check your draft against the requirements on the official page."
    return {"summary": summary, "observations": observations, "missing_topics": list(missing[:8])}


REVIEW_AGENT = Agent(
    name="application", task="draft_review", prompt_version=prompts.REVIEW_VERSION,
    system=prompts.REVIEW_SYSTEM, output_model=ReviewProposal, build_prompt=prompts.review_user,
    ground=ground_review, fallback=build_rule_review, max_output_tokens=1500,
)