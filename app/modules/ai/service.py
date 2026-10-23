import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.ai.llm import ProviderError, get_provider
from app.modules.ai.models import AIRun
from app.modules.auth.models import User
from app.modules.profiles import service as profiles_service
from app.modules.profiles.schemas import ProfileUpdate

SCALARS = ("country", "nationality", "education_level", "institution", "field_of_study",
           "graduation_year", "remote_preference", "needs_financial_aid")
FINISHED = ("succeeded", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _start_of_day() -> datetime:
    return _now().replace(hour=0, minute=0, second=0, microsecond=0)


def create_run(db: Session, user: User, task_type: str, input_meta: dict) -> AIRun:
    settings = get_settings()
    try:
        get_provider()
    except ProviderError:
        raise AppError(503, "ai_unavailable", "The AI service is not available right now")
    used = db.scalar(
        select(func.count()).select_from(AIRun)
        .where(AIRun.user_id == user.id, AIRun.created_at >= _start_of_day())
    )
    if used >= settings.ai_daily_run_limit:
        seconds = int((_start_of_day() + timedelta(days=1) - _now()).total_seconds())
        raise AppError(
            429, "ai_quota_exceeded",
            f"You have used your {settings.ai_daily_run_limit} AI requests for today. Try again tomorrow.",
            headers={"Retry-After": str(max(seconds, 1))},
        )
    run = AIRun(user_id=user.id, task_type=task_type, status="queued", input_meta=input_meta,
                provider=settings.ai_provider, model=settings.ai_model)
    db.add(run)
    db.commit()
    return run


def get_run(db: Session, user: User, run_id: uuid.UUID) -> AIRun:
    run = db.scalar(select(AIRun).where(AIRun.id == run_id, AIRun.user_id == user.id))
    if run is None:
        raise AppError(404, "not_found", "AI run not found")
    return run


def list_runs(db: Session, user: User, limit: int, offset: int):
    conds = [AIRun.user_id == user.id]
    total = db.scalar(select(func.count()).select_from(AIRun).where(*conds))
    rows = db.scalars(
        select(AIRun).where(*conds).order_by(AIRun.created_at.desc(), AIRun.id).limit(limit).offset(offset)
    ).all()
    return rows, total


def cancel_run(db: Session, user: User, run_id: uuid.UUID) -> AIRun:
    run = get_run(db, user, run_id)
    if run.status in FINISHED:
        raise AppError(409, "run_finished", "This AI run has already finished")
    run.cancel_requested = True  # a running task notices this before its next step or call
    if run.status == "queued":
        run.status = "cancelled"
        run.finished_at = _now()
    db.commit()
    return run


def usage(db: Session, user: User) -> dict:
    settings = get_settings()
    runs, tokens_in, tokens_out, cost = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(AIRun.tokens_in), 0),
            func.coalesce(func.sum(AIRun.tokens_out), 0),
            func.coalesce(func.sum(AIRun.cost_usd), 0),
        ).where(AIRun.user_id == user.id, AIRun.created_at >= _start_of_day())
    ).one()
    return {
        "date": _now().date().isoformat(), "runs_today": runs, "daily_limit": settings.ai_daily_run_limit,
        "tokens_in_today": int(tokens_in), "tokens_out_today": int(tokens_out),
        "cost_usd_today": round(float(cost), 6), "provider": settings.ai_provider, "model": settings.ai_model,
    }


def apply_profile_proposal(db: Session, user: User, run_id: uuid.UUID, fields: list[str] | None) -> dict:
    """The human approval step: nothing from an AI proposal is saved until this is called."""
    run = get_run(db, user, run_id)
    if run.task_type != "profile_extract" or run.status != "succeeded":
        raise AppError(422, "not_applicable", "This run has no profile proposal to apply")
    if run.applied_at is not None:
        raise AppError(409, "already_applied", "This proposal has already been applied")

    proposal = (run.output or {}).get("proposal") or {}
    chosen = list(dict.fromkeys(fields)) if fields is not None else list(proposal)
    if not chosen:
        raise AppError(422, "nothing_to_apply", "The AI found nothing it could confirm in your text")
    missing = [f for f in chosen if f not in proposal]
    if missing:
        raise AppError(
            422, "invalid_fields", "These fields are not in the proposal, or could not be confirmed in your text",
            missing,
        )

    profile = profiles_service.get_or_create_profile(db, user)
    changes, scalar_update = [], {}
    for field in chosen:
        if field in SCALARS:
            scalar_update[field] = proposal[field]
            changes.append({"field": field, "before": getattr(profile, field), "after": proposal[field]})

    skills_before = [ps.name for ps in profile.skills]
    new_skills = [s for s in proposal.get("skills", []) if s not in skills_before] if "skills" in chosen else []
    interests_before = list(profile.career_interests or [])
    new_interests = (
        [i for i in proposal.get("career_interests", []) if i not in interests_before]
        if "career_interests" in chosen else []
    )
    if len(skills_before) + len(new_skills) > 100:
        raise AppError(422, "too_many_skills", "A profile can have at most 100 skills")
    if len(interests_before) + len(new_interests) > 20:
        raise AppError(422, "too_many_interests", "A profile can have at most 20 career interests")

    try:
        validated = ProfileUpdate.model_validate(scalar_update).model_dump(exclude_unset=True)
    except ValidationError:
        raise AppError(422, "invalid_proposal", "The proposal contains a value that is not valid")

    if validated:
        profiles_service.update_profile(db, user, validated)
    if new_interests:
        profiles_service.update_profile(db, user, {"career_interests": interests_before + new_interests})
        changes.append({"field": "career_interests", "before": interests_before,
                        "after": interests_before + new_interests})
    if new_skills:
        existing = [{"name": ps.name, "level": ps.level} for ps in profile.skills]  # keeps existing levels
        profiles_service.replace_skills(db, user, existing + [{"name": s, "level": None} for s in new_skills])
        changes.append({"field": "skills", "before": skills_before, "after": skills_before + new_skills})

    run = get_run(db, user, run_id)
    run.applied_at = _now()
    run.output = {**(run.output or {}), "applied_fields": chosen}
    db.commit()
    return {"applied": chosen, "changes": changes}