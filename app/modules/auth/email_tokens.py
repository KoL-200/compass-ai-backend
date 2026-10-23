import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import hash_token
from app.modules.auth.models import EmailToken, User

VERIFY = "verify_email"
RESET = "reset_password"
TTL = {VERIFY: timedelta(hours=24), RESET: timedelta(minutes=30)}


def _invalid() -> AppError:
    return AppError(
        400, "invalid_or_expired_link", "This link is invalid or has expired. Request a new one."
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_token(db: Session, user: User, purpose: str) -> str:
    """Create a link token. The caller commits. A new link replaces any earlier unused one."""
    now = _now()
    db.execute(
        delete(EmailToken).where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
    )
    db.execute(delete(EmailToken).where(EmailToken.expires_at < now - timedelta(days=7)))
    raw = secrets.token_urlsafe(32)
    db.add(
        EmailToken(
            user_id=user.id, purpose=purpose, token_hash=hash_token(raw), expires_at=now + TTL[purpose]
        )
    )
    return raw


def find_valid(db: Session, raw: str, purpose: str) -> tuple[EmailToken, User]:
    """Look a link up without using it up. The purpose must match, so a verification
    link can never be used to reset a password, or the other way round."""
    row = db.scalar(
        select(EmailToken).where(EmailToken.token_hash == hash_token(raw), EmailToken.purpose == purpose)
    )
    if row is None or row.used_at is not None or row.expires_at < _now():
        raise _invalid()
    user = db.get(User, row.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise _invalid()
    return row, user


def claim(db: Session, row: EmailToken) -> None:
    """Use the link up in one atomic step, so two simultaneous requests cannot both succeed."""
    claimed = db.execute(
        update(EmailToken)
        .where(EmailToken.id == row.id, EmailToken.used_at.is_(None))
        .values(used_at=_now())
        .returning(EmailToken.id)
    ).scalar_one_or_none()
    if claimed is None:
        db.rollback()
        raise _invalid()