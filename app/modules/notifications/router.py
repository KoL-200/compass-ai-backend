import secrets
import uuid

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.jobs import run_job
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.notifications import jobs  # noqa: F401  (importing registers the jobs)
from app.modules.notifications import service
from app.modules.notifications.schemas import NotificationPage

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage)
def list_notifications(
    unread: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items, total, unread_count = service.list_notifications(
        db, user, unread=unread, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "unread_count": unread_count, "limit": limit, "offset": offset}


@router.post("/read-all", status_code=204)
def read_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service.mark_all_read(db, user)
    return Response(status_code=204)


@router.patch("/{notification_id}/read", status_code=204)
def read_one(
    notification_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    service.mark_read(db, user, notification_id)
    return Response(status_code=204)


# ---------- internal: called by the scheduler, never by end users ----------

internal_router = APIRouter(prefix="/internal/jobs", tags=["internal"], include_in_schema=False)


def require_job_secret(x_job_secret: str | None = Header(default=None)) -> None:
    configured = get_settings().job_secret
    if len(configured) < 16:
        raise AppError(503, "jobs_disabled", "Internal jobs are not configured")
    if x_job_secret is None or not secrets.compare_digest(x_job_secret.encode(), configured.encode()):
        raise AppError(401, "invalid_job_secret", "Invalid or missing job secret")


@internal_router.post("/{job_name}", dependencies=[Depends(require_job_secret)])
def trigger_job(job_name: str):
    return run_job(job_name)