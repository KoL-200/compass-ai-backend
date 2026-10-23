import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.careers import service
from app.modules.careers.schemas import CareerPathOut, RoleFamilyOut, SkillsGapOut, SkillsToLearnOut

router = APIRouter(tags=["careers"])


@router.get("/opportunities/{opp_id}/skills-gap", response_model=SkillsGapOut)
def skills_gap(opp_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.skills_gap_for_opportunity(db, user, opp_id)


@router.get("/careers/skills-to-learn", response_model=SkillsToLearnOut)
def skills_to_learn(
    category: Literal["job", "internship"] | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.skills_to_learn(db, user, category, limit)


@router.get("/careers/role-families", response_model=list[RoleFamilyOut])
def role_families(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.role_families(db)


@router.get("/careers/paths", response_model=CareerPathOut)
def career_path(
    role_family: str = Query(min_length=2, max_length=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.career_path(db, user, role_family)