from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.strategy.models import Strategy, StrategyTask

DUE_SOON_DAYS = 3
LIMIT = 20


def task_overview(db: Session, user: User, today: date) -> dict:
    """Counts and the urgent steps from the user's ACCEPTED plans. Drafts never count."""
    rows = db.execute(
        select(StrategyTask, Strategy)
        .join(Strategy, StrategyTask.strategy_id == Strategy.id)
        .where(Strategy.user_id == user.id, Strategy.status == "accepted")
    ).all()
    done = open_ = 0
    overdue, due_soon = [], []
    for task, strategy in rows:
        if task.status == "done":
            done += 1
            continue
        if task.status != "todo":  # skipped steps do not count
            continue
        open_ += 1
        if task.due_date is None:
            continue
        days = (task.due_date - today).days
        item = {"id": task.id, "title": task.title, "due_date": task.due_date,
                "opportunity": strategy.opportunity.title, "days": days}
        if days < 0:
            overdue.append(item)
        elif days <= DUE_SOON_DAYS:
            due_soon.append(item)
    key = lambda t: (t["due_date"], t["title"])
    return {"done": done, "open": open_, "overdue": sorted(overdue, key=key)[:LIMIT],
            "due_soon": sorted(due_soon, key=key)[:LIMIT]}