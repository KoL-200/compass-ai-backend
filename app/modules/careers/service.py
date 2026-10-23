import uuid
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.auth.models import User
from app.modules.careers import paths, skills_gap
from app.modules.eligibility import engine
from app.modules.eligibility import service as eligibility_service
from app.modules.opportunities import service as opportunities_service
from app.modules.profiles import service as profiles_service

MAX_POSTINGS = 500
SKILLS_NOTE = (
    "Based on open jobs and internships you are not ruled out of. It shows which skills appear "
    "most often, not a promise that learning them leads to an offer."
)
PATH_NOTE = (
    "Based only on the open postings currently in our database. It shows what employers are "
    "asking for, not a guarantee of progression."
)


def _context(db: Session, user: User):
    profile = profiles_service.get_or_create_profile(db, user)
    snapshot = eligibility_service.build_snapshot(profile)
    now = datetime.now(timezone.utc)
    skills = {s.lower() for s in snapshot["skills"]}
    months = engine.experience_months(snapshot["experiences"], now.date())
    return snapshot, skills, months, now


def _skill_lists(opp):
    detail = opp.job if opp.category == "job" else opp.internship if opp.category == "internship" else None
    if detail is None:
        return None
    return {
        "required": list(detail.required_skills or []),
        "preferred": list(getattr(detail, "preferred_skills", None) or []),
        "min_months": detail.min_experience_months or 0,
    }


def skills_gap_for_opportunity(db: Session, user: User, opp_id: uuid.UUID) -> dict:
    opp = opportunities_service.get_opportunity(db, opp_id, public=True)  # drafts are 404
    lists = _skill_lists(opp)
    if lists is None:
        raise AppError(422, "not_applicable", "Skills-gap analysis applies to jobs and internships")
    _, skills, months, _ = _context(db, user)
    gap = skills_gap.analyze_gap(skills, lists["required"], lists["preferred"], lists["min_months"], months)
    return {"opportunity": {"id": opp.id, "title": opp.title, "category": opp.category}, **gap}


def skills_to_learn(db: Session, user: User, category: str | None, limit: int) -> dict:
    snapshot, skills, _, now = _context(db, user)
    postings, excluded = [], 0
    for cat in [category] if category else ["job", "internship"]:
        opps, _ = opportunities_service.list_opportunities(
            db, public=True, category=cat, limit=MAX_POSTINGS, offset=0
        )
        for opp in opps:
            lists = _skill_lists(opp)
            if lists is None:
                continue
            result = engine.evaluate(
                snapshot, opp.requirements,
                data_quality=opp.data_quality, last_verified_at=opp.last_verified_at, now=now,
            )
            if result["status"] == "ineligible":  # a skill cannot fix a rule you fail
                excluded += 1
                continue
            postings.append(
                {"id": str(opp.id), "title": opp.title,
                 "required_skills": lists["required"], "preferred_skills": lists["preferred"]}
            )
    return {
        "postings_considered": len(postings),
        "excluded_ineligible": excluded,
        "skills_recorded": bool(skills),
        "skills": skills_gap.rank_skills_to_learn(skills, postings, limit),
        "note": SKILLS_NOTE,
    }


def _open_jobs(db: Session):
    opps, _ = opportunities_service.list_opportunities(
        db, public=True, category="job", limit=MAX_POSTINGS, offset=0
    )
    return [o for o in opps if o.job is not None]


def role_families(db: Session) -> list[dict]:
    counts = Counter(o.job.role_family for o in _open_jobs(db) if o.job.role_family)
    return [{"role_family": f, "postings": n} for f, n in sorted(counts.items())]


def career_path(db: Session, user: User, role_family: str) -> dict:
    family = role_family.strip().lower()
    _, skills, months, _ = _context(db, user)
    jobs = _open_jobs(db)
    rows = [
        {
            c: getattr(o.job, c)
            for c in ("job_level", "required_skills", "min_experience_months",
                      "salary_min", "salary_max", "salary_currency", "salary_period")
        }
        for o in jobs
        if (o.job.role_family or "") == family
    ]
    if not rows:
        available = sorted({o.job.role_family for o in jobs if o.job.role_family})
        raise AppError(404, "role_family_not_found", f"No open postings for '{family}'", available)
    return {**paths.build_path(family, rows, skills, months), "note": PATH_NOTE}

context = _context
skill_lists = _skill_lists