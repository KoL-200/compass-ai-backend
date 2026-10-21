"""Decides WHEN a reminder is due and WHAT it says. Pure Python."""
from datetime import datetime, timezone


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def due_reminder(deadline: datetime, reminder_days: list[int], now: datetime):
    """Return (threshold, days_left) or None.

    Days are calendar days in UTC. We pick the most urgent threshold that has been
    reached, so a missed cron run sends one late reminder, not a burst of them.
    """
    if deadline <= now:
        return None
    days_left = (_utc(deadline).date() - _utc(now).date()).days
    reached = [d for d in reminder_days if days_left <= d]
    if not reached:
        return None
    return min(reached), days_left


def reminder_key(application_id, deadline: datetime, threshold: int) -> str:
    """Including the deadline means a moved deadline produces fresh reminders."""
    return f"deadline:{application_id}:{_utc(deadline).isoformat()}:{threshold}"


def _clip(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def build_reminder(
    *,
    opportunity_title: str,
    organization: str,
    deadline: datetime,
    days_left: int,
    personal: bool,
    status: str,
    next_action: str | None,
) -> tuple[str, str]:
    when = "is today" if days_left == 0 else "is tomorrow" if days_left == 1 else f"is in {days_left} days"
    label = "Your target date" if personal else "Deadline"
    title = _clip(f"{label} {when}: {opportunity_title}")
    body = (
        f"{organization}: {label.lower()} is {_utc(deadline):%d %b %Y} (UTC). "
        f"Current status: {status.replace('_', ' ')}."
    )
    if next_action:
        body += f" Next step: {next_action}"
    return title, body


def build_expiry(opportunity_title: str, organization: str) -> tuple[str, str]:
    title = _clip(f"Deadline passed: {opportunity_title}")
    body = (
        f"The deadline for {opportunity_title} at {organization} has passed, so we marked your "
        f"application as expired. If you did submit it, open the application and record your "
        f"submission date."
    )
    return title, body