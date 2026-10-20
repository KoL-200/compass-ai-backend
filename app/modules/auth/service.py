import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_dummy,
    verify_password,
)
from app.modules.auth.models import RefreshToken, Role, User
from app.modules.auth.schemas import TokenPair


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad_credentials() -> AppError:
    return AppError(401, "invalid_credentials", "Invalid email or password")


def _issue_tokens(db: Session, user: User, family_id: uuid.UUID | None = None):
    s = get_settings()
    raw = generate_refresh_token()
    row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=hash_token(raw),
        family_id=family_id or uuid.uuid4(),
        expires_at=_now() + timedelta(days=s.refresh_token_days),
    )
    db.add(row)
    db.flush()
    pair = TokenPair(
        access_token=create_access_token(user.id, [r.name for r in user.roles]),
        refresh_token=raw,
        expires_in=s.access_token_minutes * 60,
    )
    return row, pair


def register(db: Session, email: str, password: str) -> User:
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise AppError(409, "email_taken", "An account with this email already exists")
    role = db.scalar(select(Role).where(Role.name == "user"))
    if role is None:
        raise RuntimeError("Roles not seeded. Run: alembic upgrade head")
    user = User(email=email, password_hash=hash_password(password), roles=[role])
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # two simultaneous registrations
        db.rollback()
        raise AppError(409, "email_taken", "An account with this email already exists")
    return user


def login(db: Session, email: str, password: str) -> TokenPair:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    usable = user is not None and user.is_active and user.deleted_at is None
    if not usable:
        verify_dummy(password)
        raise _bad_credentials()
    if not verify_password(user.password_hash, password):
        raise _bad_credentials()
    user.last_login_at = _now()
    _, pair = _issue_tokens(db, user)
    db.commit()
    return pair


def _revoke_family(db: Session, family_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


def refresh(db: Session, raw_token: str) -> TokenPair:
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if token is None:
        raise AppError(401, "invalid_token", "Invalid refresh token")

    if token.revoked_at is not None:
        # An already-used token came back: assume theft, kill the whole chain.
        _revoke_family(db, token.family_id)
        db.commit()
        raise AppError(401, "token_reused", "Refresh token was already used. Please log in again.")

    if token.expires_at < _now():
        raise AppError(401, "token_expired", "Refresh token expired")

    user = db.get(User, token.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise AppError(401, "invalid_token", "Invalid refresh token")

    new_row, pair = _issue_tokens(db, user, family_id=token.family_id)
    token.revoked_at = _now()
    token.replaced_by_id = new_row.id
    db.commit()
    return pair


def logout(db: Session, raw_token: str) -> None:
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if token is not None:
        _revoke_family(db, token.family_id)
        db.commit()
    # Unknown tokens are ignored on purpose: logout is idempotent.