import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.goals import service
from app.modules.goals.schemas import GoalCreate, GoalOut, GoalStatus, GoalUpdate

router = APIRouter(prefix="/goals", tags=["goals"])


@router.post("", response_model=GoalOut, status_code=201)
def create_goal(body: GoalCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_goal(db, user, body.model_dump())


@router.get("", response_model=list[GoalOut])
def list_goals(
    status: GoalStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows, _ = service.list_goals(db, user, status=status, limit=limit, offset=offset)
    return rows


@router.patch("/{goal_id}", response_model=GoalOut)
def update_goal(
    goal_id: uuid.UUID,
    body: GoalUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.update_goal(db, user, goal_id, body.model_dump(exclude_unset=True))


@router.delete("/{goal_id}", status_code=204)
def delete_goal(goal_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service.delete_goal(db, user, goal_id)
    return Response(status_code=204)