import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.eligibility import engine
from app.modules.eligibility.models import EligibilityResult
from app.modules.opportunities import service as opportunities_service
from app.modules.profiles import service as profiles_service


def build_snapshot(profile) -> dict:
    """Turn ORM rows into the plain dict the pure engine understands."""
    return {
        "country": profile.country,
        "nationality": profile.nationality,
        "education_level": profile.education_level,
        "field_of_study": profile.field_of_study,
        "graduation_year": profile.graduation_year,
        "needs_financial_aid": profile.needs_financial_aid,
        "date_of_birth": profile.date_of_birth,
        "skills": [ps.name for ps in profile.skills],
        "qualifications": [
            {
                "type": q.type,
                "subject_results": q.subject_results or {},
                "overall_score": q.overall_score,
                "score_scale": q.score_scale,
                "year": q.year,
            }
            for q in profile.qualifications
        ],
        "experiences": [
            {"start_date": e.start_date, "end_date": e.end_date} for e in profile.experiences
        ],
    }


def _find(db: Session, user_id: uuid.UUID, opportunity_id: uuid.UUID):
    return db.scalar(
        select(EligibilityResult).where(
            EligibilityResult.user_id == user_id,
            EligibilityResult.opportunity_id == opportunity_id,
        )
    )


def evaluate_for_user(db: Session, user: User, opp_id: uuid.UUID) -> EligibilityResult:
    profile = profiles_service.get_or_create_profile(db, user)
    opp = opportunities_service.get_opportunity(db, opp_id, public=True)  # drafts are 404
    now = datetime.now(timezone.utc)

    result = engine.evaluate(
        build_snapshot(profile),
        opp.requirements,
        data_quality=opp.data_quality,
        last_verified_at=opp.last_verified_at,
        now=now,
    )

    row = _find(db, user.id, opp.id)
    if row is None:
        row = EligibilityResult(user_id=user.id, opportunity_id=opp.id)
        db.add(row)
    row.status = result["status"]
    row.details = {
        "requirements": result["requirements"],
        "counts": result["counts"],
        "data_freshness": result["data_freshness"],
    }
    row.explanation = result["explanation"]
    row.ruleset_version = opp.requirements_version
    row.engine_version = result["engine_version"]
    row.evaluated_at = now
    try:
        db.commit()
    except IntegrityError:  # two simultaneous evaluations: update the winner's row
        db.rollback()
        row = _find(db, user.id, opp.id)
        row.status = result["status"]
        row.details = {
            "requirements": result["requirements"],
            "counts": result["counts"],
            "data_freshness": result["data_freshness"],
        }
        row.explanation = result["explanation"]
        row.ruleset_version = opp.requirements_version
        row.engine_version = result["engine_version"]
        row.evaluated_at = now
        db.commit()
    return row


def list_results(db: Session, user: User, status: str | None, limit: int, offset: int):
    conds = [EligibilityResult.user_id == user.id]  # users only ever see their own
    if status:
        conds.append(EligibilityResult.status == status)
    total = db.scalar(select(func.count()).select_from(EligibilityResult).where(*conds))
    rows = (
        db.scalars(
            select(EligibilityResult)
            .where(*conds)
            .order_by(EligibilityResult.evaluated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .unique()
        .all()
    )
    return rows, total