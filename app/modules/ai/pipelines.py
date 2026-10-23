import uuid

from sqlalchemy.orm import Session

from app.modules.ai.agents.explain import EXPLAIN_AGENT
from app.modules.ai.agents.profile import PROFILE_AGENT
from app.modules.ai.agents.strategy import STRATEGY_AGENT
from app.modules.ai.orchestrator import Pipeline, Step
from app.modules.eligibility import service as eligibility_service
from app.modules.strategy import service as strategy_service


def _opportunity_id(ctx: dict) -> uuid.UUID:
    return uuid.UUID(ctx["input"]["opportunity_id"])


def _evaluate(ctx: dict, db: Session) -> dict:
    """Runs the deterministic engine (and stores its result). The model never sees raw profile data."""
    row = eligibility_service.evaluate_for_user(db, ctx["user"], _opportunity_id(ctx))
    return eligibility_service.payload(row)


def _facts(ctx: dict, db: Session) -> dict:
    return strategy_service.build_facts(db, ctx["user"], _opportunity_id(ctx), bool(ctx["input"].get("force")))


def _save(ctx: dict, db: Session) -> dict:
    strategy = strategy_service.save_draft(
        db, ctx["user"], _opportunity_id(ctx), ctx["plan"], ctx["plan_source"], ctx["run_id"], ctx["facts"]
    )
    return {"strategy_id": str(strategy.id), "warnings": list(strategy.warnings), "task_count": len(strategy.tasks)}


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
    "strategy_generate": Pipeline(
        task="strategy_generate",
        steps=[
            Step(name="facts", kind="service", run=_facts),
            Step(name="plan", kind="agent", agent=STRATEGY_AGENT, depends_on=("facts",),
                 payload=lambda ctx: ctx["facts"]),
            Step(name="save", kind="service", depends_on=("plan",), run=_save),
        ],
        result=lambda ctx: {
            "strategy_id": ctx["save"]["strategy_id"],
            "source": ctx["plan_source"],
            "warnings": ctx["save"]["warnings"],
            "eligibility_status": ctx["facts"]["status"],
        },
        requires_approval=True,  # the plan is a draft until the user accepts it
    ),
    # The explainer and the planner both depend only on the facts, so they run in parallel.
    "guidance": Pipeline(
        task="guidance",
        steps=[
            Step(name="facts", kind="service", run=_facts),
            Step(name="explain", kind="agent", agent=EXPLAIN_AGENT, depends_on=("facts",),
                 payload=lambda ctx: ctx["facts"]),
            Step(name="plan", kind="agent", agent=STRATEGY_AGENT, depends_on=("facts",),
                 payload=lambda ctx: ctx["facts"]),
            Step(name="save", kind="service", depends_on=("plan",), run=_save),
        ],
        result=lambda ctx: {
            "eligibility_status": ctx["facts"]["status"],
            "explanation": ctx["explain"],
            "explanation_source": ctx["explain_source"],
            "strategy_id": ctx["save"]["strategy_id"],
            "plan_source": ctx["plan_source"],
            "warnings": ctx["save"]["warnings"],
        },
        requires_approval=True,
    ),
}