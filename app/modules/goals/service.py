import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.auth.models import User
from app.modules.goals.models import Goal

MAX_ACTIVE_GOALS = 10


def _get_owned(db: Session, user: User, goal_id: uuid.UUID) -> Goal:
    goal = db.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == user.id))
    if goal is None:
        raise AppError(404, "not_found", "Goal not found")
    return goal


def _active_count(db: Session, user: User) -> int:
    return db.scalar(
        select(func.count()).select_from(Goal).where(Goal.user_id == user.id, Goal.status == "active")
    )


def _limit_error() -> AppError:
    return AppError(
        422, "goal_limit", f"You can have at most {MAX_ACTIVE_GOALS} active goals. Finish or drop one first."
    )


def create_goal(db: Session, user: User, data: dict) -> Goal:
    if _active_count(db, user) >= MAX_ACTIVE_GOALS:
        raise _limit_error()
    goal = Goal(user_id=user.id, **data)
    db.add(goal)
    db.commit()
    return goal


def list_goals(db: Session, user: User, *, status: str | None, limit: int, offset: int):
    conds = [Goal.user_id == user.id]  # users only ever see their own
    if status:
        conds.append(Goal.status == status)
    total = db.scalar(select(func.count()).select_from(Goal).where(*conds))
    rows = db.scalars(
        select(Goal).where(*conds).order_by(Goal.created_at.desc(), Goal.id).limit(limit).offset(offset)
    ).all()
    return rows, total


def update_goal(db: Session, user: User, goal_id: uuid.UUID, patch: dict) -> Goal:
    goal = _get_owned(db, user, goal_id)
    for key in ("title", "status"):
        if key in patch and patch[key] is None:
            raise AppError(422, "validation_error", f"{key} cannot be null")
    if patch.get("status") == "active" and goal.status != "active" and _active_count(db, user) >= MAX_ACTIVE_GOALS:
        raise _limit_error()
    for key, value in patch.items():
        setattr(goal, key, value)
    db.commit()
    return goal


def delete_goal(db: Session, user: User, goal_id: uuid.UUID) -> None:
    db.delete(_get_owned(db, user, goal_id))
    db.commit()