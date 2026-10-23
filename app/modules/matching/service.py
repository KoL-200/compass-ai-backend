from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.auth.models import User
from app.modules.eligibility import engine
from app.modules.eligibility import service as eligibility_service
from app.modules.matching import scoring
from app.modules.matching.models import Match
from app.modules.opportunities import service as opportunities_service
from app.modules.opportunities.models import Opportunity
from app.modules.profiles import service as profiles_service

MAX_OPPORTUNITIES = 500


def build_preferences(profile, today) -> dict:
    interests: list[str] = []
    for term in [profile.field_of_study, *(profile.career_interests or [])]:
        clean = term.strip().lower() if term else ""
        if clean and clean not in interests:
            interests.append(clean)
    return {
        "interests": interests,
        "skills": {ps.name for ps in profile.skills},
        "country": profile.country,
        "education_level": profile.education_level,
        "needs_financial_aid": profile.needs_financial_aid,
        "remote_preference": profile.remote_preference,
        "preferred_locations": [p.strip().lower() for p in (profile.preferred_locations or []) if p.strip()],
        "experience_months": engine.experience_months(
            [{"start_date": e.start_date, "end_date": e.end_date} for e in profile.experiences], today
        ),
    }


def opportunity_view(opp: Opportunity) -> dict:
    detail = getattr(opp, opp.category, None)
    details = (
        {c.key: getattr(detail, c.key) for c in detail.__table__.columns if c.key != "opportunity_id"}
        if detail is not None
        else {}
    )
    return {
        "category": opp.category,
        "title": opp.title,
        "country": opp.country,
        "location": opp.location,
        "work_mode": opp.work_mode,
        "deadline_at": opp.deadline_at,
        "details": details,
    }


def refresh_matches(db: Session, user: User) -> dict:
    profile = profiles_service.get_or_create_profile(db, user)
    snapshot = eligibility_service.build_snapshot(profile)
    now = datetime.now(timezone.utc)
    prefs = build_preferences(profile, now.date())

    opportunities, total = opportunities_service.list_opportunities(
        db, public=True, limit=MAX_OPPORTUNITIES, offset=0
    )

    rows, by_status = [], {}
    for opp in opportunities:
        eligibility = engine.evaluate(
            snapshot,
            opp.requirements,
            data_quality=opp.data_quality,
            last_verified_at=opp.last_verified_at,
            now=now,
        )
        result = scoring.score_match(prefs, opportunity_view(opp), eligibility, now)
        by_status[result["eligibility_status"]] = by_status.get(result["eligibility_status"], 0) + 1
        rows.append(Match(user_id=user.id, opportunity_id=opp.id, calculated_at=now, **result))

    # Replace the user's matches in one transaction: simple, and stale rows vanish.
    db.execute(delete(Match).where(Match.user_id == user.id))
    db.add_all(rows)
    try:
        db.commit()
    except IntegrityError:  # two refreshes at the same moment
        db.rollback()
        raise AppError(409, "refresh_in_progress", "A refresh is already running. Try again in a moment.")

    return {
        "computed": len(rows),
        "truncated": total > MAX_OPPORTUNITIES,
        "by_status": by_status,
        "algorithm_version": scoring.ALGORITHM_VERSION,
    }


def list_matches(
    db: Session,
    user: User,
    *,
    category: str | None,
    status: str | None,
    include_ineligible: bool,
    limit: int,
    offset: int,
):
    now = datetime.now(timezone.utc)
    conds = [
        Match.user_id == user.id,  # users only ever see their own
        Opportunity.status == "open",  # hide anything archived or closed since the last refresh
        or_(Opportunity.deadline_at.is_(None), Opportunity.deadline_at > now),
    ]
    if category:
        conds.append(Opportunity.category == category)
    if status:
        conds.append(Match.eligibility_status == status)
    elif not include_ineligible:
        conds.append(Match.eligibility_status != "ineligible")

    join_on = Match.opportunity_id == Opportunity.id
    total = db.scalar(select(func.count()).select_from(Match).join(Opportunity, join_on).where(*conds))
    stmt = (
        select(Match)
        .join(Opportunity, join_on)
        .where(*conds)
        .order_by(Match.score.desc(), Opportunity.deadline_at.asc().nulls_last(), Match.id)
        .limit(limit)
        .offset(offset)
    )
    return db.scalars(stmt).unique().all(), total

def last_computed_at(db: Session, user: User) -> datetime | None:
    return db.scalar(select(func.max(Match.calculated_at)).where(Match.user_id == user.id))