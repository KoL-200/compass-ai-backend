import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.auth.models import User
from app.modules.notifications.models import Notification


def create_notification(
    db: Session, *, user_id: uuid.UUID, type: str, title: str, body: str, data: dict, dedupe_key: str
) -> bool:
    """Insert once. Returns False when this dedupe_key already exists (safe to call repeatedly)."""
    stmt = (
        pg_insert(Notification)
        .values(
            id=uuid.uuid4(), user_id=user_id, type=type, channel="in_app",
            title=title, body=body, data=data, dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Notification.id)
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def unread_count(db: Session, user: User) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )


def list_notifications(db: Session, user: User, *, unread: bool, limit: int, offset: int):
    conds = [Notification.user_id == user.id]  # users only ever see their own
    if unread:
        conds.append(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(Notification).where(*conds))
    rows = db.scalars(
        select(Notification)
        .where(*conds)
        .order_by(Notification.created_at.desc(), Notification.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return rows, total, unread_count(db, user)


def mark_read(db: Session, user: User, notification_id: uuid.UUID) -> None:
    n = db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if n is None:
        raise AppError(404, "not_found", "Notification not found")
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        db.commit()


def mark_all_read(db: Session, user: User) -> None:
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(timezone.utc))
    )
    db.commit()