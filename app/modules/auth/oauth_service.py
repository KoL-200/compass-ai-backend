import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import hash_token
from app.modules.auth import audit, service
from app.modules.auth.models import OAuthAccount, OAuthLoginCode, Role, User
from app.modules.auth.oauth import Identity
from app.modules.auth.schemas import TokenPair

CODE_TTL_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise AppError(403, "account_disabled", "This account is disabled")
    return user


def _find_link(db: Session, identity: Identity) -> OAuthAccount | None:
    return db.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == identity.provider,
            OAuthAccount.provider_user_id == identity.subject,
        )
    )


def resolve_user(db: Session, identity: Identity, ip: str | None = None) -> User:
    """Find the account for a provider identity, linking or creating it when needed."""
    link = _find_link(db, identity)
    if link is not None:
        return _active_user(db, link.user_id)

    now = _now()
    user = db.scalar(select(User).where(User.email == identity.email))
    created_user = user is None
    if user is not None:
        if not user.is_active or user.deleted_at is not None:
            raise AppError(403, "account_disabled", "This account is disabled")
        if user.email_verified_at is None:
            # We never confirmed this email, so whoever registered it with a password may not
            # own it. The provider has confirmed ownership, so remove that password and end
            # every session it may have created.
            user.password_hash = None
            user.failed_login_count = 0
            user.locked_until = None
            user.email_verified_at = now
            service.revoke_all_tokens(db, user.id)
            user.token_version += 1
            audit.record(
                db, "password_removed_on_link", user_id=user.id, ip=ip, provider=identity.provider
            )
        audit.record(db, "oauth_linked", user_id=user.id, ip=ip, provider=identity.provider)
    else:
        role = db.scalar(select(Role).where(Role.name == "user"))
        if role is None:
            raise RuntimeError("Roles not seeded. Run: alembic upgrade head")
        user = User(
            id=uuid.uuid4(),
            email=identity.email,
            password_hash=None,
            email_verified_at=now,
            roles=[role],
        )
        db.add(user)

    try:
        if created_user:
            db.flush()
            audit.record(db, "register", user_id=user.id, ip=ip, method=identity.provider)
        db.add(
            OAuthAccount(
                user_id=user.id,
                provider=identity.provider,
                provider_user_id=identity.subject,
                email=identity.email,
            )
        )
        db.commit()
    except IntegrityError:  # a parallel request created the same link first
        db.rollback()
        link = _find_link(db, identity)
        if link is not None:
            return _active_user(db, link.user_id)
        raise AppError(
            409, "link_conflict", "This sign-in could not be linked. Use the method you used before."
        )
    return user


def create_login_code(db: Session, user: User) -> str:
    now = _now()
    db.execute(delete(OAuthLoginCode).where(OAuthLoginCode.expires_at < now - timedelta(hours=1)))
    raw = secrets.token_urlsafe(32)
    db.add(
        OAuthLoginCode(
            user_id=user.id,
            code_hash=hash_token(raw),
            expires_at=now + timedelta(seconds=CODE_TTL_SECONDS),
        )
    )
    db.commit()
    return raw


def exchange_login_code(db: Session, raw_code: str, ip: str | None = None) -> TokenPair:
    invalid = AppError(400, "invalid_code", "The sign-in code is invalid or has expired")
    now = _now()
    row = db.scalar(select(OAuthLoginCode).where(OAuthLoginCode.code_hash == hash_token(raw_code)))
    if row is None or row.expires_at < now:
        raise invalid
    # Mark it used in one atomic step, so two simultaneous requests cannot both succeed.
    claimed = db.execute(
        update(OAuthLoginCode)
        .where(OAuthLoginCode.id == row.id, OAuthLoginCode.used_at.is_(None))
        .values(used_at=now)
        .returning(OAuthLoginCode.id)
    ).scalar_one_or_none()
    if claimed is None:
        db.rollback()
        raise invalid
    user = _active_user(db, row.user_id)
    return service.start_session(db, user, method="oauth", ip=ip)