from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.core.db import SessionLocal
from app.core.jobs import job
from app.modules.ai.models import AIRun

QUEUED_MAX_MINUTES = 15
RUNNING_MAX_MINUTES = 10


@job("ai-cleanup")
def cleanup() -> dict:
    """Closes runs that can no longer finish (for example, the server restarted mid-run)."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        queued = db.execute(
            update(AIRun)
            .where(AIRun.status == "queued", AIRun.created_at < now - timedelta(minutes=QUEUED_MAX_MINUTES))
            .values(status="failed", error_code="never_started",
                    error="The request was never started. Please try again.", finished_at=now)
        ).rowcount
        running = db.execute(
            update(AIRun)
            .where(AIRun.status == "running", AIRun.started_at < now - timedelta(minutes=RUNNING_MAX_MINUTES))
            .values(status="failed", error_code="interrupted",
                    error="The request was interrupted. Please try again.", finished_at=now)
        ).rowcount
        db.commit()
    return {"queued_failed": queued, "running_failed": running}