import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.auth.models import User
from app.modules.careers import service as careers_service
from app.modules.careers import skills_gap
from app.modules.dashboard import completeness
from app.modules.dashboard import service as dashboard_service
from app.modules.eligibility import service as eligibility_service
from app.modules.opportunities import service as opportunities_service
from app.modules.profiles import service as profiles_service
from app.modules.strategy import planning
from app.modules.strategy.models import Strategy, StrategyTask

MAX_TASKS_PER_PLAN = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- facts for the planner ----------

def build_facts(db: Session, user: User, opp_id: uuid.UUID, force: bool = False) -> dict:
    """Everything a plan may be based on. Runs the deterministic eligibility engine first."""
    opp = opportunities_service.get_opportunity(db, opp_id, public=True)  # drafts are 404
    now = _now()
    if opp.status != "open" or (opp.deadline_at is not None and opp.deadline_at <= now):
        raise AppError(422, "opportunity_closed", "This opportunity is closed or its deadline has passed")

    row = eligibility_service.evaluate_for_user(db, user, opp.id)
    if row.status == "ineligible" and not force:
        raise AppError(
            422, "not_recommended",
            "You do not currently meet a documented requirement for this opportunity. Review the "
            "requirements first, or ask for a plan anyway.",
        )

    _, skills, months, _ = careers_service.context(db, user)
    lists = careers_service.skill_lists(opp)
    missing_skills, experience_gap = [], None
    if lists is not None:
        gap = skills_gap.analyze_gap(skills, lists["required"], lists["preferred"], lists["min_months"], months)
        missing_skills, experience_gap = gap["required"]["missing"], gap["experience"]["gap_months"]

    profile = profiles_service.get_or_create_profile(db, user)
    assessment = completeness.assess(dashboard_service.profile_view(profile))
    return {
        **eligibility_service.payload(row),
        "today": now.date().isoformat(),
        "deadline_date": opp.deadline_at.date().isoformat() if opp.deadline_at else None,
        "days_left": (opp.deadline_at - now).days if opp.deadline_at else None,
        "missing_skills": missing_skills,
        "skills_known": bool(skills),
        "experience_gap_months": experience_gap,
        "profile_missing": [m["label"] for m in assessment["missing"] if opp.category in m["helps"]],
    }


def save_draft(db: Session, user: User, opp_id: uuid.UUID, plan: dict, source: str,
               run_id: uuid.UUID | None, facts: dict) -> Strategy:
    deadline = date.fromisoformat(facts["deadline_date"]) if facts.get("deadline_date") else None
    scheduled, warnings = planning.schedule(plan["tasks"], date.fromisoformat(facts["today"]), deadline)

    # Earlier drafts for the same opportunity are replaced. Accepted plans are never touched here.
    db.execute(
        update(Strategy)
        .where(Strategy.user_id == user.id, Strategy.opportunity_id == opp_id, Strategy.status == "draft")
        .values(status="rejected")
    )
    strategy = Strategy(
        user_id=user.id, opportunity_id=opp_id, status="draft",
        source="ai" if source == "ai" else "rules",
        summary=plan["summary"], warnings=warnings, run_id=run_id,
    )
    for position, t in enumerate(scheduled, start=1):
        strategy.tasks.append(
            StrategyTask(
                position=position, title=t["title"], detail=t["detail"], kind=t["kind"],
                due_date=t["due_date"], effort_days=t["effort_days"], status="todo",
                origin="ai" if source == "ai" else "rules",
            )
        )
    db.add(strategy)
    db.commit()
    return strategy


# ---------- reading ----------

def get_owned(db: Session, user: User, strategy_id: uuid.UUID) -> Strategy:
    stmt = (
        select(Strategy)
        .where(Strategy.id == strategy_id, Strategy.user_id == user.id)  # other people's plans are 404
        .execution_options(populate_existing=True)
    )
    strategy = db.scalar(stmt)
    if strategy is None:
        raise AppError(404, "not_found", "Plan not found")
    return strategy


def list_strategies(db: Session, user: User, *, opportunity_id, status, limit: int, offset: int):
    conds = [Strategy.user_id == user.id]
    if opportunity_id:
        conds.append(Strategy.opportunity_id == opportunity_id)
    if status:
        conds.append(Strategy.status == status)
    total = db.scalar(select(func.count()).select_from(Strategy).where(*conds))
    rows = db.scalars(
        select(Strategy).where(*conds).order_by(Strategy.created_at.desc(), Strategy.id).limit(limit).offset(offset)
    ).all()
    return rows, total


# ---------- decisions ----------

def set_status(db: Session, user: User, strategy_id: uuid.UUID, new_status: str) -> Strategy:
    strategy = get_owned(db, user, strategy_id)
    if new_status == "accepted":
        if strategy.status != "draft":
            raise AppError(409, "not_a_draft", "Only a draft plan can be accepted")
        db.execute(  # the new plan replaces the one the user accepted before
            update(Strategy)
            .where(Strategy.user_id == user.id, Strategy.opportunity_id == strategy.opportunity_id,
                   Strategy.status == "accepted")
            .values(status="superseded")
        )
        strategy.status = "accepted"
        strategy.accepted_at = _now()
    else:
        if strategy.status not in ("draft", "accepted"):
            raise AppError(409, "not_open", "This plan is already closed")
        strategy.status = "rejected"
    try:
        db.commit()
    except IntegrityError:  # two simultaneous accepts
        db.rollback()
        raise AppError(409, "accept_conflict", "Another plan was accepted at the same moment. Try again.")
    return get_owned(db, user, strategy_id)


# ---------- editing tasks ----------

def _editable(strategy: Strategy) -> None:
    if strategy.status not in ("draft", "accepted"):
        raise AppError(409, "read_only", "This plan is closed and can no longer be edited")


def _get_task(strategy: Strategy, task_id: uuid.UUID) -> StrategyTask:
    task = next((t for t in strategy.tasks if t.id == task_id), None)
    if task is None:
        raise AppError(404, "not_found", "Task not found")
    return task


def add_task(db: Session, user: User, strategy_id: uuid.UUID, data: dict) -> StrategyTask:
    strategy = get_owned(db, user, strategy_id)
    _editable(strategy)
    if len(strategy.tasks) >= MAX_TASKS_PER_PLAN:
        raise AppError(422, "task_limit", f"A plan can have at most {MAX_TASKS_PER_PLAN} tasks")
    position = max((t.position for t in strategy.tasks), default=0) + 1
    task = StrategyTask(position=position, kind="custom", status="todo", origin="user", **data)
    strategy.tasks.append(task)
    db.commit()
    return task


def update_task(db: Session, user: User, strategy_id: uuid.UUID, task_id: uuid.UUID, patch: dict) -> StrategyTask:
    strategy = get_owned(db, user, strategy_id)
    _editable(strategy)
    task = _get_task(strategy, task_id)
    for key in ("title", "effort_days", "status"):
        if key in patch and patch[key] is None:
            raise AppError(422, "validation_error", f"{key} cannot be null")
    if "status" in patch and patch["status"] != task.status:
        task.status = patch["status"]
        task.done_at = _now() if task.status == "done" else None
    if "detail" in patch:
        task.detail = patch["detail"] or ""
    for key in ("title", "due_date", "effort_days"):
        if key in patch:
            setattr(task, key, patch[key])
    db.commit()
    return task


def delete_task(db: Session, user: User, strategy_id: uuid.UUID, task_id: uuid.UUID) -> None:
    strategy = get_owned(db, user, strategy_id)
    _editable(strategy)
    strategy.tasks.remove(_get_task(strategy, task_id))
    db.commit()