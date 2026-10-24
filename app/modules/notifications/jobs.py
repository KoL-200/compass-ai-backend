from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.jobs import job
from app.modules.applications.models import Application, ApplicationEvent
from app.modules.applications.transitions import PRE_SUBMISSION
from app.modules.notifications import reminders, service
from app.modules.opportunities.models import Opportunity

import logging

from app.core.errors import AppError
from app.modules.ai.jobs import cleanup as ai_cleanup  # also registers the ai-cleanup job

log = logging.getLogger(__name__)

BATCH = 500
HORIZON_DAYS = 91  # reminder_days can be at most 90


def scan_deadlines(db: Session, now: datetime) -> dict:
    """Create reminder notifications. Safe to run any number of times."""
    effective = func.coalesce(Application.personal_deadline_at, Opportunity.deadline_at)
    stmt = (
        select(Application)
        .join(Opportunity, Application.opportunity_id == Opportunity.id)
        .where(
            Application.status.in_(PRE_SUBMISSION),
            Application.reminders_enabled.is_(True),
            Opportunity.status == "open",
            effective > now,
            effective <= now + timedelta(days=HORIZON_DAYS),
        )
        .order_by(effective, Application.id)
        .limit(BATCH)
    )
    apps = db.scalars(stmt).unique().all()

    created = 0
    for app in apps:
        deadline = app.effective_deadline_at
        due = reminders.due_reminder(deadline, app.reminder_days, now)
        if due is None:
            continue
        threshold, days_left = due
        opp = app.opportunity
        title, body = reminders.build_reminder(
            opportunity_title=opp.title,
            organization=opp.organization.name,
            deadline=deadline,
            days_left=days_left,
            personal=app.personal_deadline_at is not None,
            status=app.status,
            next_action=app.next_action,
        )
        created += service.create_notification(
            db,
            user_id=app.user_id,
            type="deadline_reminder",
            title=title,
            body=body,
            data={
                "application_id": str(app.id),
                "opportunity_id": str(opp.id),
                "days_left": days_left,
                "threshold_days": threshold,
                "deadline_at": deadline.isoformat(),
            },
            dedupe_key=reminders.reminder_key(app.id, deadline, threshold),
        )
    db.commit()
    return {"checked": len(apps), "notifications": created, "truncated": len(apps) == BATCH}


def expire_applications(db: Session, now: datetime) -> dict:
    """Mark unsubmitted applications as expired once the OFFICIAL deadline has passed."""
    stmt = (
        select(Application)
        .join(Opportunity, Application.opportunity_id == Opportunity.id)
        .where(
            Application.status.in_(PRE_SUBMISSION),
            Opportunity.deadline_at.is_not(None),
            Opportunity.deadline_at <= now,
        )
        .order_by(Opportunity.deadline_at, Application.id)
        .limit(BATCH)
        .with_for_update(skip_locked=True, of=Application)  # two sweeps never process the same row
    )
    apps = db.scalars(stmt).unique().all()

    created = 0
    for app in apps:
        opp = app.opportunity
        app.events.append(
            ApplicationEvent(
                from_status=app.status,
                to_status="expired",
                note="Deadline passed without a submission being recorded",
                actor="system",
                occurred_at=now,
            )
        )
        app.status = "expired"
        title, body = reminders.build_expiry(opp.title, opp.organization.name)
        created += service.create_notification(
            db,
            user_id=app.user_id,
            type="application_expired",
            title=title,
            body=body,
            data={"application_id": str(app.id), "opportunity_id": str(opp.id)},
            dedupe_key=f"expired:{app.id}:{opp.deadline_at.isoformat()}",
        )
    db.commit()
    return {"expired": len(apps), "notifications": created, "truncated": len(apps) == BATCH}


# ---------- registered jobs (what the cron endpoint can run) ----------

def _now() -> datetime:
    return datetime.now(timezone.utc)


@job("expire-applications")
def run_expire() -> dict:
    with SessionLocal() as db:
        return expire_applications(db, _now())


@job("scan-deadlines")
def run_scan() -> dict:
    with SessionLocal() as db:
        return scan_deadlines(db, _now())


@job("daily")
def run_daily() -> dict:
    """Runs every step even if one fails, then reports. Expire first, so a lapsed deadline gets no reminder."""
    report, failed = {}, []
    for name, step in (
        ("expire-applications", run_expire),
        ("scan-deadlines", run_scan),
        ("ai-cleanup", ai_cleanup),
    ):
        try:
            report[name] = step()
        except Exception:
            log.exception("Daily job step '%s' failed", name)
            failed.append(name)
    if failed:
        raise AppError(500, "job_failed", "One or more daily jobs failed. See the logs.", failed)
    return report