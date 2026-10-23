from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.dashboard import service
from app.modules.dashboard.schemas import DashboardOut, NextAction

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.build_dashboard(db, user)


@router.get("/next-actions", response_model=list[NextAction])
def next_actions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.build_dashboard(db, user)["next_actions"]