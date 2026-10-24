"""A deterministic stand-in for a real model. It uses keyword rules and templates."""
import json
import re

from app.modules.ai import knowledge as k
from app.modules.ai.explain_text import build_explanation
from app.modules.ai.llm import LLMRequest, LLMResponse, ProviderError
from app.modules.strategy.planning import build_rule_plan
from app.modules.ai.agents.application import build_rule_checklist, build_rule_review
from app.modules.ai.agents.compare import build_rule_comparison
from app.modules.ai.agents.discover import build_rule_picks
from app.modules.ai.agents.tracker import build_rule_summary

INTENT = re.compile(r"\b(want to|wants to|hope to|plan to|planning|apply|applying|pursue|pursuing|seeking|looking to)\b", re.I)
FIELD = re.compile(
    r"(?:studying|studied|study|degree in|majoring in|major in|majored in|graduated in)\s+"
    r"([A-Za-z][A-Za-z &-]{2,60}?)(?=\s+(?:at|in|from|and|with)\b|[,.;\n]|$)", re.I)
INTEREST = re.compile(
    r"(?:interested in|passionate about|career in|work in|work as an?|become an?)\s+"
    r"([A-Za-z][A-Za-z ,&-]{2,80}?)(?=[.;\n]|$)", re.I)
INSTITUTION = re.compile(
    r"\b((?:the )?University of [A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?|"
    r"[A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)? (?:University|Institute|College))\b")

def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _education(text: str) -> str | None:
    found = []
    for sentence in _sentences(text):
        if INTENT.search(sentence):  # "I want to do a master's" is a plan, not a qualification
            continue
        for level in k.LEVEL_ORDER:
            if any(k.contains_term(sentence, term) for term in k.LEVEL_KEYWORDS[level]):
                found.append(level)
    return max(found, key=k.LEVEL_ORDER.index) if found else None


def _countries(text: str) -> tuple[str | None, str | None]:
    nationality = next((code for word, code in k.DEMONYMS.items() if k.contains_term(text, word)), None)
    country = None
    match = re.search(r"\b(?:live|lives|living|based|residing|reside|resident)\s+in\s+([A-Za-z ,]+)", text)
    if match:
        segment = match.group(1).lower()
        for code, aliases in k.COUNTRY_ALIASES.items():
            if any(k.contains_term(segment, a) for a in aliases if a not in k.DEMONYMS):
                country = code
                break
    return country, nationality


def _interests(text: str) -> list[str]:
    found: list[str] = []
    for match in INTEREST.finditer(text):
        for part in re.split(r"\s*(?:,|\band\b|&)\s*", match.group(1).lower()):
            part = part.strip()
            if 3 <= len(part) <= 60 and part not in found:
                found.append(part)
    return found[:10]


def _profile(payload: dict) -> dict:
    text = payload["text"]
    country, nationality = _countries(text)
    field = FIELD.search(text)
    institution = INSTITUTION.search(text)
    year = re.search(r"(?:graduat\w*|finish\w*|complet\w*)[^.\n]{0,40}?\b(20\d{2})\b", text, re.I)
    low = text.lower()
    aid = False if any(p in low for p in k.AID_NO) else True if any(p in low for p in k.AID_YES) else None
    remote = next(
        (name for name, words in k.REMOTE_KEYWORDS.items() if any(w in low for w in words)), None
    )
    return {
        "country": country,
        "nationality": nationality,
        "education_level": _education(text),
        "institution": re.sub(r"^the ", "", institution.group(1)) if institution else None,
        "field_of_study": field.group(1).strip().lower() if field else None,
        "graduation_year": int(year.group(1)) if year else None,
        "skills": [s for s in k.KNOWN_SKILLS if k.contains_term(text, s)][:30],
        "career_interests": _interests(text),
        "remote_preference": remote,
        "needs_financial_aid": aid,
    }


def _explain(payload: dict) -> dict:
    return {"status": payload["status"], **build_explanation(payload)}


HANDLERS = {
    "profile_extract": _profile,
    "eligibility_explain": _explain,
    "strategy_plan": build_rule_plan,
    "opportunity_discover": build_rule_picks,
    "opportunity_compare": build_rule_comparison,
    "application_checklist": build_rule_checklist,
    "draft_review": build_rule_review,
    "progress_summary": build_rule_summary,
}


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockProvider:
    name = "mock"

    def complete(self, request: LLMRequest) -> LLMResponse:
        handler = HANDLERS.get(request.task)
        if handler is None:
            raise ProviderError(f"The mock provider has no handler for '{request.task}'")
        text = json.dumps(handler(request.payload))
        return LLMResponse(
            text=text,
            tokens_in=_tokens(request.system + request.user),
            tokens_out=_tokens(text),
            model="mock-1",
        )