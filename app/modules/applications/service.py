import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.applications.models import Application, ApplicationEvent, ChecklistItem
from app.modules.applications.transitions import (
    INACTIVE,
    PRE_SUBMISSION,
    TRANSITIONS,
    USER_CANNOT_SET,
)
from app.modules.auth.models import User
from app.modules.opportunities import service as opportunities_service
from app.modules.opportunities.models import Opportunity

MAX_CHECKLIST_ITEMS = 50
DELETABLE = PRE_SUBMISSION | {"expired"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_owned(db: Session, user: User, app_id: uuid.UUID) -> Application:
    """Every lookup is scoped to the user: other people's applications are simply 'not found'."""
    stmt = (
        select(Application)
        .where(Application.id == app_id, Application.user_id == user.id)
        .execution_options(populate_existing=True)
    )
    app = db.scalar(stmt)
    if app is None:
        raise AppError(404, "not_found", "Application not found")
    return app


def create_application(db: Session, user: User, opp_id: uuid.UUID) -> Application:
    opp = opportunities_service.get_opportunity(db, opp_id, public=True)  # drafts/archived: 404
    now = _now()
    if opp.status != "open" or (opp.deadline_at is not None and opp.deadline_at <= now):
        raise AppError(
            422, "opportunity_closed", "This opportunity is closed or its deadline has passed"
        )

    app = Application(user_id=user.id, opportunity_id=opp.id, status="interested")
    app.events.append(
        ApplicationEvent(
            from_status=None, to_status="interested", note=None, actor="user", occurred_at=now
        )
    )
    db.add(app)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "already_tracking", "You are already tracking this opportunity")
    return _get_owned(db, user, app.id)


def get_application(db: Session, user: User, app_id: uuid.UUID) -> Application:
    return _get_owned(db, user, app_id)


def list_applications(
    db: Session,
    user: User,
    *,
    status: str | None,
    active: bool | None,
    limit: int,
    offset: int,
):
    conds = [Application.user_id == user.id]
    if status:
        conds.append(Application.status == status)
    if active is True:
        conds.append(Application.status.notin_(INACTIVE))
    elif active is False:
        conds.append(Application.status.in_(INACTIVE))

    join_on = Application.opportunity_id == Opportunity.id
    effective_deadline = func.coalesce(Application.personal_deadline_at, Opportunity.deadline_at)
    total = db.scalar(select(func.count()).select_from(Application).join(Opportunity, join_on).where(*conds))
    stmt = (
        select(Application)
        .join(Opportunity, join_on)
        .where(*conds)
        .order_by(effective_deadline.asc().nulls_last(), Application.created_at.desc(), Application.id)
        .limit(limit)
        .offset(offset)
    )
    return db.scalars(stmt).all(), total


def _transition(
    app: Application,
    opp: Opportunity,
    new_status: str,
    note: str | None,
    submitted_at: datetime | None,
    now: datetime,
) -> None:
    if new_status in USER_CANNOT_SET:
        raise AppError(
            422, "status_not_allowed", "'expired' is set automatically when a deadline passes"
        )
    allowed = TRANSITIONS[app.status]
    if new_status not in allowed:
        raise AppError(
            422,
            "invalid_transition",
            f"Cannot move an application from '{app.status}' to '{new_status}'",
            sorted(allowed),
        )
    if app.status == "expired" and new_status != "submitted":
        reopened = opp.status == "open" and (opp.deadline_at is None or opp.deadline_at > now)
        if not reopened:
            raise AppError(
                422, "deadline_passed", "This opportunity is closed or its deadline has passed"
            )
    if new_status == "submitted":
        app.submitted_at = submitted_at or now  # reported by the user
    app.events.append(
        ApplicationEvent(
            from_status=app.status, to_status=new_status, note=note, actor="user", occurred_at=now
        )
    )
    app.status = new_status


def update_application(db: Session, user: User, app_id: uuid.UUID, patch: dict) -> Application:
    app = _get_owned(db, user, app_id)
    opp = app.opportunity
    now = _now()

    new_status = patch.pop("status", None)
    note = patch.pop("note", None)
    submitted_at = patch.pop("submitted_at", None)

    for key in ("reminders_enabled", "reminder_days"):
        if key in patch and patch[key] is None:
            raise AppError(422, "validation_error", f"{key} cannot be null")
    personal = patch.get("personal_deadline_at")
    if personal is not None and opp.deadline_at is not None and personal > opp.deadline_at:
        raise AppError(
            422, "validation_error", "personal_deadline_at cannot be after the official deadline"
        )
    if submitted_at is not None and submitted_at > now:
        raise AppError(422, "validation_error", "submitted_at cannot be in the future")

    if new_status is not None and new_status != app.status:
        _transition(app, opp, new_status, note, submitted_at, now)
    elif new_status == "submitted" and submitted_at is not None:
        app.submitted_at = submitted_at  # correcting the date you reported

    for key, value in patch.items():
        setattr(app, key, value)
    db.commit()
    return _get_owned(db, user, app.id)


def delete_application(db: Session, user: User, app_id: uuid.UUID) -> None:
    app = _get_owned(db, user, app_id)
    if app.status not in DELETABLE or app.submitted_at is not None:
        raise AppError(
            409,
            "cannot_delete",
            "Submitted applications are part of your history and cannot be deleted. "
            "Withdraw it instead.",
        )
    db.delete(app)
    db.commit()


def timeline(db: Session, user: User, app_id: uuid.UUID) -> list[ApplicationEvent]:
    app = _get_owned(db, user, app_id)
    stmt = (
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == app.id)
        .order_by(ApplicationEvent.occurred_at, ApplicationEvent.id)
    )
    return db.scalars(stmt).all()


# ---------- checklist ----------

def add_checklist_item(db: Session, user: User, app_id: uuid.UUID, label: str) -> ChecklistItem:
    app = _get_owned(db, user, app_id)
    if len(app.checklist) >= MAX_CHECKLIST_ITEMS:
        raise AppError(
            422, "checklist_full", f"A checklist can have at most {MAX_CHECKLIST_ITEMS} items"
        )
    item = ChecklistItem(label=label)
    app.checklist.append(item)
    db.commit()
    return item


def _get_item(db: Session, app: Application, item_id: uuid.UUID) -> ChecklistItem:
    item = db.scalar(
        select(ChecklistItem).where(
            ChecklistItem.id == item_id, ChecklistItem.application_id == app.id
        )
    )
    if item is None:
        raise AppError(404, "not_found", "Checklist item not found")
    return item


def update_checklist_item(
    db: Session, user: User, app_id: uuid.UUID, item_id: uuid.UUID, patch: dict
) -> ChecklistItem:
    app = _get_owned(db, user, app_id)
    item = _get_item(db, app, item_id)
    if patch.get("label") is not None:
        item.label = patch["label"]
    if patch.get("is_done") is not None and patch["is_done"] != item.is_done:
        item.is_done = patch["is_done"]
        item.done_at = _now() if item.is_done else None
    db.commit()
    return item


def delete_checklist_item(db: Session, user: User, app_id: uuid.UUID, item_id: uuid.UUID) -> None:
    app = _get_owned(db, user, app_id)
    item = _get_item(db, app, item_id)
    app.checklist.remove(item)  # delete-orphan removes the row
    db.commit()