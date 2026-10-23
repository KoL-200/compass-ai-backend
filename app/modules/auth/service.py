import math
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.passwords import check_password_policy
from app.core.ratelimit import hash_ident
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_dummy,
    verify_password,
)
from app.modules.auth import audit
from app.modules.auth.models import RefreshToken, Role, User
from app.modules.auth.schemas import TokenPair


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad_credentials() -> AppError:
    return AppError(401, "invalid_credentials", "Invalid email or password")


def _locked(until: datetime, now: datetime) -> AppError:
    seconds = max(1, int((until - now).total_seconds()))
    minutes = math.ceil(seconds / 60)
    return AppError(
        429,
        "account_locked",
        f"Too many failed sign-in attempts. Try again in {minutes} minute(s).",
        headers={"Retry-After": str(seconds)},
    )


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
        access_token=create_access_token(user.id, [r.name for r in user.roles], user.token_version),
        refresh_token=raw,
        expires_in=s.access_token_minutes * 60,
    )
    return row, pair


def _revoke_all_tokens(db: Session, user_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


def register(db: Session, email: str, password: str, ip: str | None = None) -> User:
    email = email.strip().lower()
    check_password_policy(password, email)
    if db.scalar(select(User).where(User.email == email)):
        raise AppError(409, "email_taken", "An account with this email already exists")
    role = db.scalar(select(Role).where(Role.name == "user"))
    if role is None:
        raise RuntimeError("Roles not seeded. Run: alembic upgrade head")
    user = User(id=uuid.uuid4(), email=email, password_hash=hash_password(password), roles=[role])
    db.add(user)
    try:
        db.flush()
        audit.record(db, "register", user_id=user.id, ip=ip, method="password")
        db.commit()
    except IntegrityError:  # two simultaneous registrations
        db.rollback()
        raise AppError(409, "email_taken", "An account with this email already exists")
    return user


def _register_failure(db: Session, user: User, ip: str | None, now: datetime) -> None:
    s = get_settings()
    count = db.execute(
        update(User)
        .where(User.id == user.id)
        .values(failed_login_count=User.failed_login_count + 1)
        .returning(User.failed_login_count)
    ).scalar_one()
    audit.record(db, "login_failed", user_id=user.id, ip=ip, reason="bad_password", failures=count)
    if count >= s.login_max_failures:
        until = now + timedelta(minutes=s.login_lock_minutes)
        db.execute(
            update(User).where(User.id == user.id).values(failed_login_count=0, locked_until=until)
        )
        audit.record(db, "account_locked", user_id=user.id, ip=ip, until=until.isoformat())
    db.commit()  # must be saved before the caller raises the error


def login(db: Session, email: str, password: str, ip: str | None = None) -> TokenPair:
    email = email.strip().lower()
    now = _now()
    user = db.scalar(select(User).where(User.email == email))

    if user is None or not user.is_active or user.deleted_at is not None:
        verify_dummy(password)  # same cost as a real check, so timing reveals nothing
        audit.record(db, "login_failed", ip=ip, reason="unknown_account", email_hash=hash_ident(email))
        db.commit()
        raise _bad_credentials()

    if user.locked_until is not None and user.locked_until > now:
        # While locked we do not even check the password.
        audit.record(db, "login_blocked", user_id=user.id, ip=ip)
        db.commit()
        raise _locked(user.locked_until, now)

    if user.password_hash is None:  # signs in with Google or GitHub only
        verify_dummy(password)
        _register_failure(db, user, ip, now)
        raise _bad_credentials()
    if not verify_password(user.password_hash, password):
        _register_failure(db, user, ip, now)
        raise _bad_credentials()
    if get_settings().require_verified_email and user.email_verified_at is None:
        audit.record(db, "login_blocked", user_id=user.id, ip=ip, reason="email_not_verified")
        db.commit()
        raise AppError(
            403, "email_not_verified", "Confirm your email address first. Check your inbox or request a new link."
        )

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    _, pair = _issue_tokens(db, user)
    audit.record(db, "login_success", user_id=user.id, ip=ip, method="password")
    db.commit()
    return pair


def refresh(db: Session, raw_token: str, ip: str | None = None) -> TokenPair:
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if token is None:
        raise AppError(401, "invalid_token", "Invalid refresh token")

    if token.revoked_at is not None:
        # An already-used token came back: assume theft and end the whole chain.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        audit.record(db, "refresh_reuse_detected", user_id=token.user_id, ip=ip)
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


def logout(db: Session, raw_token: str, ip: str | None = None) -> None:
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if token is not None:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        audit.record(db, "logout", user_id=token.user_id, ip=ip)
        db.commit()
    # Unknown tokens are ignored on purpose: logout is idempotent.


def logout_all(db: Session, user: User, ip: str | None = None) -> None:
    _revoke_all_tokens(db, user.id)
    user.token_version += 1  # also kills access tokens that are still within their 15 minutes
    audit.record(db, "logout_all", user_id=user.id, ip=ip)
    db.commit()


def change_password(
    db: Session, user: User, current_password: str, new_password: str, ip: str | None = None
) -> TokenPair:
    if user.password_hash is None:
        raise AppError(
            422, "no_password_set", "This account signs in with Google or GitHub and has no password"
        )
    if not verify_password(user.password_hash, current_password):
        audit.record(db, "password_change_failed", user_id=user.id, ip=ip)
        db.commit()
        raise AppError(400, "invalid_current_password", "The current password is incorrect")
    check_password_policy(new_password, user.email)
    if verify_password(user.password_hash, new_password):
        raise AppError(422, "password_unchanged", "The new password must be different")

    user.password_hash = hash_password(new_password)
    _revoke_all_tokens(db, user.id)
    user.token_version += 1
    _, pair = _issue_tokens(db, user)  # this session stays signed in; all others end
    audit.record(db, "password_changed", user_id=user.id, ip=ip)
    db.commit()
    return pair

def revoke_all_tokens(db: Session, user_id: uuid.UUID) -> None:
    _revoke_all_tokens(db, user_id)


def start_session(db: Session, user: User, *, method: str, ip: str | None = None) -> TokenPair:
    """Issue a token pair for a user who has already been authenticated by some method."""
    user.last_login_at = _now()
    _, pair = _issue_tokens(db, user)
    audit.record(db, "login_success", user_id=user.id, ip=ip, method=method)
    db.commit()
    return pair