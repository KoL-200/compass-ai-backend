import uuid

from sqlalchemy.orm import Session

from app.modules.ai.agents.explain import EXPLAIN_AGENT
from app.modules.ai.agents.profile import PROFILE_AGENT
from app.modules.ai.agents.strategy import STRATEGY_AGENT
from app.modules.ai.orchestrator import Pipeline, Step
from app.modules.eligibility import service as eligibility_service
from app.modules.strategy import service as strategy_service
from app.modules.ai import facts as ai_facts
from app.modules.ai.agents.application import CHECKLIST_AGENT, REVIEW_AGENT
from app.modules.ai.agents.compare import COMPARE_AGENT
from app.modules.ai.agents.discover import DISCOVER_AGENT
from app.modules.ai.agents.tracker import TRACKER_AGENT
from app.modules.matching.schemas import SCORE_NOTE
from app.modules.notifications import service as notifications_service


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

def _app_id(ctx: dict) -> uuid.UUID:
    return uuid.UUID(ctx["input"]["application_id"])


def _discover_result(ctx: dict) -> dict:
    by_id = {c["opportunity_id"]: c for c in ctx["candidates"]["candidates"]}
    items = []
    for pick in ctx["select"]["picks"]:
        c = by_id[pick["opportunity_id"]]
        items.append({
            "opportunity_id": c["opportunity_id"], "title": c["title"], "category": c["category"],
            "organization": c["organization"], "score": c["score"],
            "eligibility_status": c["eligibility_status"], "deadline": c["deadline"],
            "goal_aligned": c["goal_aligned"], "reason": pick["reason"],
            "caveats": c["data_flags"],  # computed from our data, never written by the model
        })
    return {"overview": ctx["select"]["overview"], "items": items,
            "candidates_considered": len(ctx["candidates"]["candidates"]),
            "already_tracking": ctx["candidates"]["already_tracking"],
            "source": ctx["select_source"], "note": SCORE_NOTE}


def _compare_result(ctx: dict) -> dict:
    text = {c["opportunity_id"]: c for c in ctx["compare"]["comparisons"]}
    keys = ("opportunity_id", "title", "category", "organization", "score", "eligibility_status", "deadline")
    items = [
        {**{k: i[k] for k in keys}, "data_flags": i["data_flags"],
         "strengths": text[i["opportunity_id"]]["strengths"], "trade_offs": text[i["opportunity_id"]]["trade_offs"]}
        for i in ctx["facts"]["items"]
    ]
    return {"summary": ctx["compare"]["summary"], "best_fit_id": ctx["facts"]["best_fit_id"],
            "items": items, "alternatives": ctx["facts"]["alternatives"],
            "source": ctx["compare_source"], "note": SCORE_NOTE}


def _notify(ctx: dict, db: Session) -> dict:
    if not ctx["input"].get("notify"):
        return {"created": False}
    summary, user = ctx["summary"], ctx["user"]
    created = notifications_service.create_notification(
        db, user_id=user.id, type="progress_summary", title=summary["headline"][:200],
        body="\n".join([*summary["highlights"], *summary["attention"]]) or summary["headline"],
        data={"run_id": str(ctx["run_id"])}, dedupe_key=f"progress:{user.id}:{ctx['facts']['today']}",
    )
    return {"created": created}  # the step runner commits after this returns


REVIEW_NOTE = ("This is feedback only. Compass AI does not write or change your application, and you are "
               "responsible for everything you submit.")
CHECKLIST_NOTE = ("These are suggestions. Items marked 'standard' are common for this kind of application, "
                  "not confirmed requirements. Check the official page.")

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
    "opportunity_discover": Pipeline(
        task="opportunity_discover",
        steps=[
            Step(name="candidates", kind="service", run=lambda ctx, db: ai_facts.discover_candidates(
                db, ctx["user"], category=ctx["input"].get("category"), q=ctx["input"].get("q"),
                limit=ctx["input"]["limit"])),
            Step(name="select", kind="agent", agent=DISCOVER_AGENT, depends_on=("candidates",),
                 payload=lambda ctx: ctx["candidates"]),
        ],
        result=_discover_result,
    ),
    "opportunity_compare": Pipeline(
        task="opportunity_compare",
        steps=[
            Step(name="facts", kind="service", run=lambda ctx, db: ai_facts.compare_facts(
                db, ctx["user"], [uuid.UUID(i) for i in ctx["input"]["opportunity_ids"]])),
            Step(name="compare", kind="agent", agent=COMPARE_AGENT, depends_on=("facts",),
                 payload=lambda ctx: ctx["facts"]),
        ],
        result=_compare_result,
    ),
    "application_checklist": Pipeline(
        task="application_checklist",
        steps=[
            Step(name="facts", kind="service",
                 run=lambda ctx, db: ai_facts.checklist_facts(db, ctx["user"], _app_id(ctx))),
            Step(name="propose", kind="agent", agent=CHECKLIST_AGENT, depends_on=("facts",),
                 payload=lambda ctx: ctx["facts"]),
        ],
        result=lambda ctx: {"application_id": ctx["facts"]["application_id"], "items": ctx["propose"]["items"],
                            "source": ctx["propose_source"], "note": CHECKLIST_NOTE},
        requires_approval=True,  # nothing is added to the checklist until the user approves
    ),
    "draft_review": Pipeline(
        task="draft_review",
        steps=[
            Step(name="facts", kind="service", run=lambda ctx, db: ai_facts.review_facts(
                db, ctx["user"], _app_id(ctx), ctx["input"]["kind"], ctx["input"]["text"])),
            Step(name="review", kind="agent", agent=REVIEW_AGENT, depends_on=("facts",),
                 payload=lambda ctx: ctx["facts"]),
        ],
        result=lambda ctx: {
            "kind": ctx["facts"]["kind"], "stats": ctx["facts"]["stats"],
            "skill_coverage": {"mentioned": ctx["facts"]["skills"]["mentioned"],
                               "not_mentioned": ctx["facts"]["skills"]["not_mentioned"]},
            **ctx["review"], "source": ctx["review_source"], "note": REVIEW_NOTE,  # the draft itself is not included
        },
    ),
    "progress_summary": Pipeline(
        task="progress_summary",
        steps=[
            Step(name="facts", kind="service", run=lambda ctx, db: ai_facts.progress_facts(db, ctx["user"])),
            Step(name="summary", kind="agent", agent=TRACKER_AGENT, depends_on=("facts",),
                 payload=lambda ctx: ctx["facts"]),
            Step(name="notify", kind="service", depends_on=("summary",), run=_notify),
        ],
        result=lambda ctx: {"summary": ctx["summary"], "source": ctx["summary_source"],
                            "notification_created": ctx["notify"]["created"],
                            "note": "Based only on what is recorded in your account. It does not predict outcomes."},
    ),
}