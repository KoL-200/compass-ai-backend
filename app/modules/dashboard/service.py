from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.applications import service as applications_service
from app.modules.applications.transitions import PRE_SUBMISSION
from app.modules.auth.models import User
from app.modules.dashboard import actions as actions_module
from app.modules.dashboard import completeness
from app.modules.goals import service as goals_service
from app.modules.matching import service as matching_service
from app.modules.notifications import service as notifications_service
from app.modules.profiles import service as profiles_service

CATEGORIES = ("admission", "scholarship", "internship", "job")
TOP_PER_CATEGORY = 3
UPCOMING_WINDOW_DAYS = 30
MAX_UPCOMING = 5
PROGRESS_NOTE = "These counts show how far you have got. They do not predict outcomes."


def _profile_view(profile) -> dict:
    return {
        "country": profile.country,
        "nationality": profile.nationality,
        "education_level": profile.education_level,
        "field_of_study": profile.field_of_study,
        "qualification_count": len(profile.qualifications),
        "skill_count": len(profile.skills),
        "interest_count": len(profile.career_interests or []),
        "has_location_preference": bool(profile.preferred_locations) or profile.remote_preference is not None,
        "needs_financial_aid": profile.needs_financial_aid,
    }


def _match_brief(match, goal_categories: set, tracked: set) -> dict:
    opp = match.opportunity
    return {
        "opportunity_id": match.opportunity_id,
        "title": opp.title,
        "category": opp.category,
        "organization": opp.organization.name,
        "score": match.score,
        "eligibility_status": match.eligibility_status,
        "deadline_at": opp.deadline_at,
        "goal_aligned": opp.category in goal_categories,
        "tracked": match.opportunity_id in tracked,
    }


def build_dashboard(db: Session, user: User) -> dict:
    now = datetime.now(timezone.utc)
    profile = profiles_service.get_or_create_profile(db, user)
    assessment = completeness.assess(_profile_view(profile))

    goals, _ = goals_service.list_goals(db, user, status="active", limit=10, offset=0)
    goal_categories = {g.category for g in goals if g.category}

    stats = applications_service.stats(db, user)
    applications, _ = applications_service.list_applications(
        db, user, status=None, active=True, limit=100, offset=0
    )
    tracked = applications_service.tracked_opportunity_ids(db, user)

    top_by_category, flat, visible_total = {}, [], 0
    for category in CATEGORIES:
        rows, total = matching_service.list_matches(
            db, user, category=category, status=None, include_ineligible=False,
            limit=TOP_PER_CATEGORY, offset=0,
        )
        visible_total += total
        items = [_match_brief(m, goal_categories, tracked) for m in rows]
        top_by_category[category] = items
        flat += items

    app_rows = [
        {
            "id": a.id,
            "title": a.opportunity.title,
            "category": a.opportunity.category,
            "status": a.status,
            "deadline_at": a.effective_deadline_at,
            "updated_at": a.updated_at,
        }
        for a in applications
    ]
    upcoming = [
        {
            "application_id": a["id"], "title": a["title"], "category": a["category"],
            "status": a["status"], "deadline_at": a["deadline_at"],
            "days_left": max(0, (a["deadline_at"] - now).days),
        }
        for a in app_rows
        if a["status"] in PRE_SUBMISSION
        and a["deadline_at"] is not None
        and now < a["deadline_at"]
        and (a["deadline_at"] - now).days <= UPCOMING_WINDOW_DAYS
    ][:MAX_UPCOMING]

    last_computed = matching_service.last_computed_at(db, user)
    actions = actions_module.next_actions(
        {
            "email_verified": user.email_verified,
            "completeness": assessment,
            "active_goals": len(goals),
            "matches_last_computed_at": last_computed,
            "matches": flat,
            "applications": app_rows,
        },
        now,
    )

    return {
        "profile": {
            "completeness_percent": assessment["percent"],
            "missing": assessment["missing"],
            "email_verified": user.email_verified,
        },
        "goals": goals,
        "applications": {
            "total": stats["total"],
            "active": stats["active"],
            "by_status": stats["by_status"],
            "by_category": stats["by_category"],
            "upcoming_deadlines": upcoming,
        },
        "matches": {
            "last_computed_at": last_computed,
            "visible_total": visible_total,
            "top_by_category": top_by_category,
        },
        "progress": {
            "matched": visible_total,
            "tracked": stats["total"],
            "submitted": stats["submitted_ever"],
            "interviews": stats["interviews_ever"],
            "offers": stats["offers_ever"],
            "note": PROGRESS_NOTE,
        },
        "unread_notifications": notifications_service.unread_count(db, user),
        "next_actions": actions,
    }

profile_view = _profile_view