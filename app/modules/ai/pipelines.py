import uuid

from sqlalchemy.orm import Session

from app.modules.ai.agents.explain import EXPLAIN_AGENT
from app.modules.ai.agents.profile import PROFILE_AGENT
from app.modules.ai.orchestrator import Pipeline, Step
from app.modules.eligibility import service as eligibility_service


def _evaluate(ctx: dict, db: Session) -> dict:
    """Runs the deterministic engine (and stores its result). The model never sees raw profile data."""
    row = eligibility_service.evaluate_for_user(db, ctx["user"], uuid.UUID(ctx["input"]["opportunity_id"]))
    opp = row.opportunity
    return {
        "status": row.status,
        "opportunity": {"title": opp.title, "category": opp.category, "organization": opp.organization.name},
        "requirements": [
            {key: r.get(key) for key in ("label", "outcome", "expected", "actual", "reason")}
            for r in row.details["requirements"]
        ],
        "counts": row.details["counts"],
        "data_freshness": row.details["data_freshness"],
        "engine_explanation": row.explanation,
    }


PIPELINES: dict[str, Pipeline] = {
    "profile_extract": Pipeline(
        task="profile_extract",
        steps=[Step(name="extract", kind="agent", agent=PROFILE_AGENT,
                    payload=lambda ctx: {"text": ctx["input"]["text"]})],
        result=lambda ctx: {**ctx["extract"], "source": ctx["extract_source"]},
        requires_approval=True,  # a human reviews the proposal before anything is saved
    ),
    "eligibility_explain": Pipeline(
        task="eligibility_explain",
        steps=[
            Step(name="evaluate", kind="service", run=_evaluate),
            Step(name="explain", kind="agent", agent=EXPLAIN_AGENT, depends_on=("evaluate",),
                 payload=lambda ctx: ctx["evaluate"]),
        ],
        result=lambda ctx: {
            "opportunity": ctx["evaluate"]["opportunity"],
            "eligibility_status": ctx["evaluate"]["status"],  # always the engine's, never the model's
            "explanation": ctx["explain"],
            "source": ctx["explain_source"],
        },
    ),
}