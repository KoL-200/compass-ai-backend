import json

from app.modules.ai.llm import LLMResponse, ProviderError

SAMPLE_TEXT = (
    "I am a Nigerian student living in Lagos, Nigeria. I am studying computer science at the "
    "University of Lagos and I will graduate in 2027. I know Python, SQL and Git. I am interested "
    "in data science and machine learning. I would like remote work. I need financial aid to fund my studies."
)


def explanation(status="potentially_eligible", **over):
    data = {"status": status, "summary": "A short, kind explanation.", "strengths": ["country is NG"],
            "gaps": [], "unknowns": [], "next_steps": ["Complete your profile."]}
    data.update(over)
    return data


def explain_payload(status="potentially_eligible"):
    return {
        "status": status,
        "opportunity": {"title": "BSc Computer Science", "category": "admission", "organization": "Uni One"},
        "requirements": [
            {"label": "country is NG", "outcome": "pass", "expected": ["NG"], "actual": "NG", "reason": None},
            {"label": "qualification score (jamb) at least 200", "outcome": "unknown", "expected": 200,
             "actual": None, "reason": "jamb score not recorded"},
        ],
        "counts": {"satisfied": 1, "unsatisfied": 0, "unknown": 1, "review": 0},
        "data_freshness": {"data_quality": "verified", "is_fresh": True},
        "engine_explanation": "No requirement was failed.",
    }


class ScriptedProvider:
    """Plays back a script: a dict or str is an answer; an exception instance is raised."""

    name = "scripted"

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        step = self.script.pop(0) if self.script else ProviderError("script exhausted")
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = step(request)
        if isinstance(step, dict):
            step = json.dumps(step)
        return LLMResponse(text=step, tokens_in=100, tokens_out=50, model="mock-1")