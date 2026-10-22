import uuid

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.modules.auth.models import User

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise AppError(401, "not_authenticated", "Missing credentials")
    try:
        payload = decode_access_token(creds.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise AppError(401, "invalid_token", "Invalid or expired token")

    user = db.get(User, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise AppError(401, "invalid_token", "Invalid or expired token")
    if payload.get("ver") != user.token_version:  # logout-everywhere or password change
        raise AppError(401, "invalid_token", "Invalid or expired token")
    return user


def require_role(*names: str):
    allowed = set(names)

    def checker(user: User = Depends(get_current_user)) -> User:
        # Roles come from the database, not the token, so a demoted admin loses access at once.
        if not {r.name for r in user.roles} & allowed:
            raise AppError(403, "forbidden", "You do not have permission to do this")
        return user

    return checker