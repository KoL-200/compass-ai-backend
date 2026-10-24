"""The facts each agent may see. All deterministic; nothing here calls a model."""
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.ai import knowledge
from app.modules.ai.agents.compare import ELIGIBLE_OK, choose_best
from app.modules.applications import service as applications_service
from app.modules.applications.transitions import INACTIVE, PRE_SUBMISSION
from app.modules.auth.models import User
from app.modules.careers import service as careers_service
from app.modules.dashboard import service as dashboard_service
from app.modules.eligibility import engine
from app.modules.goals import service as goals_service
from app.modules.matching import service as matching_service
from app.modules.opportunities import service as opportunities_service
from app.modules.strategy import tasks as strategy_tasks

MAX_CANDIDATES = 30


def _date(value) -> str | None:
    return value.date().isoformat() if value else None


def _safe(title: str) -> str:
    return title.replace("%", " percent")  # a "%" would look like a probability to our output checks


def data_flags(opp, now: datetime) -> list[str]:
    """Plain-language warnings about how far our data can be trusted. Computed, never written by a model."""
    flags = []
    if opp.source_id is None:
        flags.append("No source is recorded for this opportunity")
    if opp.data_quality == "unverified":
        flags.append("Our information has not been verified")
    elif opp.data_quality == "needs_review":
        flags.append("Our information is being re-checked")
    elif opp.last_verified_at is not None and (now - opp.last_verified_at).days > engine.STALE_AFTER_DAYS:
        flags.append(f"Last verified {(now - opp.last_verified_at).days} days ago")
    if not opp.requirements:
        flags.append("No requirements are documented, so eligibility cannot be assessed")
    if opp.deadline_at is None:
        flags.append("No deadline is recorded")
    return flags


# ---------- Opportunity Hunter ----------

def discover_candidates(db: Session, user: User, *, category, q, limit: int) -> dict:
    matching_service.refresh_matches(db, user)  # so the scores below are current
    rows, _ = matching_service.list_matches(
        db, user, category=category, status=None, include_ineligible=False, limit=MAX_CANDIDATES, offset=0
    )
    tracked = applications_service.tracked_opportunity_ids(db, user)
    goals, _ = goals_service.list_goals(db, user, status="active", limit=10, offset=0)
    goal_categories = {g.category for g in goals if g.category}
    needle = (q or "").strip().lower()
    now = datetime.now(timezone.utc)

    candidates, already_tracking = [], 0
    for m in rows:
        opp = m.opportunity
        if m.opportunity_id in tracked:
            already_tracking += 1
            continue
        if needle and needle not in f"{opp.title} {opp.organization.name}".lower():
            continue
        top = [f for f in sorted(m.factors, key=lambda f: -f["points"]) if f["points"] > 0][:2]
        candidates.append({
            "opportunity_id": str(m.opportunity_id), "title": opp.title, "category": opp.category,
            "organization": opp.organization.name, "score": m.score,
            "eligibility_status": m.eligibility_status, "deadline": _date(opp.deadline_at),
            "goal_aligned": opp.category in goal_categories, "data_flags": data_flags(opp, now),
            "top_factors": [f"{f['label']}: {f['detail']}" for f in top],
        })
    return {"limit": limit, "category": category, "query": q or None,
            "already_tracking": already_tracking, "candidates": candidates}


# ---------- Matchmaker ----------

def _compare_item(m, now: datetime) -> dict:
    opp = m.opportunity
    return {
        "opportunity_id": str(m.opportunity_id), "title": opp.title, "category": opp.category,
        "organization": opp.organization.name, "score": m.score, "eligibility_status": m.eligibility_status,
        "deadline": _date(opp.deadline_at), "location": opp.location, "work_mode": opp.work_mode,
        "factors": [{k: f[k] for k in ("label", "points", "max_points", "detail", "known")} for f in m.factors],
        "gaps": list(m.gaps)[:5], "data_flags": data_flags(opp, now),
    }


def compare_facts(db: Session, user: User, ids: list[uuid.UUID]) -> dict:
    for opp_id in ids:
        opportunities_service.get_opportunity(db, opp_id, public=True)  # hidden ones are 404
    matching_service.refresh_matches(db, user)
    rows, _ = matching_service.list_matches(
        db, user, category=None, status=None, include_ineligible=True, limit=500, offset=0
    )
    by_id = {m.opportunity_id: m for m in rows}
    missing = [str(i) for i in ids if i not in by_id]
    if missing:
        raise AppError(422, "not_comparable",
                       "These opportunities are closed or past their deadline and cannot be compared", missing)

    now = datetime.now(timezone.utc)
    items = [_compare_item(by_id[i], now) for i in ids]  # in the order requested
    best_id = choose_best(items)
    best_title = next((i["title"] for i in items if i["opportunity_id"] == best_id), None)

    wanted, categories = set(ids), {i["category"] for i in items}
    others = sorted(
        (m for m in rows if m.opportunity_id not in wanted and m.eligibility_status in ELIGIBLE_OK
         and m.opportunity.category in categories),
        key=lambda m: (-m.score, m.opportunity.title),
    )[:3]
    alternatives = [{"opportunity_id": str(m.opportunity_id), "title": m.opportunity.title,
                     "category": m.opportunity.category, "score": m.score,
                     "eligibility_status": m.eligibility_status} for m in others]
    return {"items": items, "best_fit_id": best_id, "best_fit_title": best_title, "alternatives": alternatives}


# ---------- Application agent ----------

def leaves(tree, out: list | None = None) -> list[dict]:
    out = [] if out is None else out
    if not tree:
        return out
    if tree["type"] == "LEAF":
        out.append({"label": tree.get("label") or engine.auto_label(tree), "field": tree["field"],
                    "qualification_type": tree.get("qualification_type"), "subject": tree.get("subject")})
    else:
        for child in tree["children"]:
            leaves(child, out)
    return out


def _brief(opp) -> dict:
    return {"title": opp.title, "category": opp.category, "organization": opp.organization.name}


def checklist_facts(db: Session, user: User, app_id: uuid.UUID) -> dict:
    application = applications_service.get_application(db, user, app_id)  # other people's are 404
    if application.status in INACTIVE:
        raise AppError(422, "application_closed", "This application is closed, so a checklist is not needed")
    room = min(15, applications_service.MAX_CHECKLIST_ITEMS - len(application.checklist))
    if room <= 0:
        raise AppError(422, "checklist_full", "This checklist is full")
    requirements, seen = [], set()
    for leaf in leaves(application.opportunity.requirements):
        if leaf["label"] not in seen:
            seen.add(leaf["label"])
            requirements.append(leaf)
    return {"application_id": str(application.id), "opportunity": _brief(application.opportunity),
            "requirements": requirements[:20], "existing_items": [c.label for c in application.checklist],
            "limit": room}


def review_facts(db: Session, user: User, app_id: uuid.UUID, kind: str, text: str) -> dict:
    application = applications_service.get_application(db, user, app_id)
    opp = application.opportunity
    lists = careers_service.skill_lists(opp)  # None for admissions and scholarships
    required = [s.lower() for s in lists["required"]] if lists else []
    preferred = [s.lower() for s in lists["preferred"]] if lists else []
    wanted = list(dict.fromkeys(required + preferred))
    mentioned = [s for s in wanted if knowledge.contains_term(text, s)]
    not_mentioned = [s for s in wanted if s not in mentioned]
    return {
        "kind": kind, "opportunity": _brief(opp),
        "requirements": [r["label"] for r in leaves(opp.requirements)][:20],
        "skills": {"required": required, "preferred": preferred, "mentioned": mentioned,
                   "not_mentioned": not_mentioned, "not_mentioned_count": len(not_mentioned)},
        "stats": {"words": len(text.split()), "characters": len(text),
                  "paragraphs": len([p for p in re.split(r"\n\s*\n", text) if p.strip()])},
        "text": text,  # held in memory for this run only; never stored
    }


# ---------- Tracker ----------

def progress_facts(db: Session, user: User) -> dict:
    now = datetime.now(timezone.utc)
    stats = applications_service.stats(db, user)
    apps, _ = applications_service.list_applications(db, user, status=None, active=True, limit=100, offset=0)
    upcoming = []
    for a in apps:
        deadline = a.effective_deadline_at
        if a.status in PRE_SUBMISSION and deadline is not None and now < deadline and (deadline - now).days <= 30:
            upcoming.append({"title": a.opportunity.title, "status": a.status.replace("_", " "),
                             "days_left": (deadline - now).days})
    upcoming.sort(key=lambda d: (d["days_left"], d["title"]))

    overview = strategy_tasks.task_overview(db, user, now.date())
    dashboard = dashboard_service.build_dashboard(db, user)
    return {
        "today": now.date().isoformat(),
        "applications": {"total": stats["total"], "active": stats["active"], "by_status": stats["by_status"],
                         "submitted_ever": stats["submitted_ever"], "interviews_ever": stats["interviews_ever"],
                         "offers_ever": stats["offers_ever"]},
        "upcoming_deadlines": upcoming[:5],
        "plan_tasks": {
            "done": overview["done"], "open": overview["open"], "total": overview["done"] + overview["open"],
            "overdue": [{"title": t["title"], "days_overdue": -t["days"]} for t in overview["overdue"][:5]],
            "due_soon": [{"title": t["title"], "days_left": t["days"]} for t in overview["due_soon"][:5]],
        },
        "status_changes_last_14_days": applications_service.recent_status_changes(db, user, 14),
        "next_actions": [{"title": _safe(a["title"]), "priority": a["priority"]}
                         for a in dashboard["next_actions"][:5]],
        "active_goals": len(dashboard["goals"]),
    }