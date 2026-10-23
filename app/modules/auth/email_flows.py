from datetime import datetime, timezone
from urllib.parse import urlencode

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.passwords import check_password_policy
from app.core.ratelimit import hash_ident
from app.core.security import hash_password
from app.modules.auth import audit, email_tokens, service
from app.modules.auth.models import EmailToken, User

Outgoing = tuple[str, str, str]  # (to, subject, body)


def _link(path: str, raw: str) -> str:
    return f"{get_settings().frontend_base_url.rstrip('/')}{path}?{urlencode({'token': raw})}"


def verification_message(db: Session, user: User) -> Outgoing:
    raw = email_tokens.create_token(db, user, email_tokens.VERIFY)
    db.commit()
    body = (
        "Welcome to Compass AI.\n\n"
        "Confirm your email address by opening this link (valid for 24 hours):\n"
        f"{_link('/verify-email', raw)}\n\n"
        "If you did not create an account, you can ignore this email."
    )
    return user.email, "Confirm your Compass AI email address", body


def reset_message(db: Session, user: User) -> Outgoing:
    raw = email_tokens.create_token(db, user, email_tokens.RESET)
    db.commit()
    body = (
        "We received a request to reset your Compass AI password.\n\n"
        "Open this link to choose a new one (valid for 30 minutes):\n"
        f"{_link('/reset-password', raw)}\n\n"
        "If you did not ask for this, ignore this email. Your password has not changed."
    )
    return user.email, "Reset your Compass AI password", body


def password_changed_notice(user: User) -> Outgoing:
    body = (
        "The password for your Compass AI account was just changed, and you were signed "
        "out of your other devices.\n\n"
        "If this was not you, reset your password straight away using the 'Forgot password' link."
    )
    return user.email, "Your Compass AI password was changed", body


def verify_email(db: Session, raw: str, ip: str | None = None) -> None:
    row, user = email_tokens.find_valid(db, raw, email_tokens.VERIFY)
    email_tokens.claim(db, row)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
    audit.record(db, "email_verified", user_id=user.id, ip=ip)
    db.commit()


def request_password_reset(db: Session, email: str, ip: str | None = None) -> Outgoing | None:
    """Returns the email to send, or None. The caller answers the same way either way."""
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or user.deleted_at is not None:
        audit.record(
            db, "password_reset_requested", ip=ip, reason="unknown_account", email_hash=hash_ident(email)
        )
        db.commit()
        return None
    message = reset_message(db, user)  # commits
    audit.record(db, "password_reset_requested", user_id=user.id, ip=ip)
    db.commit()
    return message


def reset_password(db: Session, raw: str, new_password: str, ip: str | None = None) -> Outgoing:
    row, user = email_tokens.find_valid(db, raw, email_tokens.RESET)
    check_password_policy(new_password, user.email)  # before claiming: a weak choice keeps the link
    email_tokens.claim(db, row)

    user.password_hash = hash_password(new_password)
    if user.email_verified_at is None:  # following the link proves they own the mailbox
        user.email_verified_at = datetime.now(timezone.utc)
    user.failed_login_count = 0
    user.locked_until = None
    user.token_version += 1
    service.revoke_all_tokens(db, user.id)
    db.execute(
        delete(EmailToken).where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == email_tokens.RESET,
            EmailToken.used_at.is_(None),
        )
    )
    audit.record(db, "password_reset", user_id=user.id, ip=ip)
    db.commit()
    return password_changed_notice(user)