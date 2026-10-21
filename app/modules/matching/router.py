from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.eligibility.schemas import EligibilityStatus
from app.modules.matching import service
from app.modules.matching.schemas import MatchPage, RefreshOut
from app.modules.opportunities.schemas import Category

router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("/refresh", response_model=RefreshOut)
def refresh(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.refresh_matches(db, user)


@router.get("", response_model=MatchPage)
def my_matches(
    category: Category | None = None,
    status: EligibilityStatus | None = None,
    include_ineligible: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows, total = service.list_matches(
        db, user, category=category, status=status,
        include_ineligible=include_ineligible, limit=limit, offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}