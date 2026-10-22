from sqlalchemy.orm import Session

from app.modules.auth.models import AuthEvent


def record(db: Session, event: str, *, user_id=None, ip: str | None = None, **detail) -> None:
    """Add an event to the current transaction. It is saved when the caller commits."""
    db.add(AuthEvent(user_id=user_id, event=event, ip=ip, detail=detail))