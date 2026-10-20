import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.profiles import service
from app.modules.profiles.schemas import (
    ExperienceCreate,
    ExperienceOut,
    ProfileOut,
    ProfileUpdate,
    QualificationCreate,
    QualificationOut,
    QualificationUpdate,
    SkillOut,
    SkillsReplace,
)

router = APIRouter(prefix="/profiles/me", tags=["profile"])


def _profile_out(profile) -> ProfileOut:
    out = ProfileOut.model_validate(profile)
    return out.model_copy(update={"missing_fields": service.missing_fields(profile)})


@router.get("", response_model=ProfileOut)
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _profile_out(service.get_or_create_profile(db, user))


@router.put("", response_model=ProfileOut)
def update_profile(
    body: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    # Only fields the client actually sent are changed.
    return _profile_out(service.update_profile(db, user, body.model_dump(exclude_unset=True)))


@router.get("/qualifications", response_model=list[QualificationOut])
def list_qualifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.get_or_create_profile(db, user).qualifications


@router.post("/qualifications", response_model=QualificationOut, status_code=201)
def add_qualification(
    body: QualificationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service.add_qualification(db, user, body.model_dump())


@router.patch("/qualifications/{qid}", response_model=QualificationOut)
def update_qualification(
    qid: uuid.UUID,
    body: QualificationUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.update_qualification(db, user, qid, body.model_dump(exclude_unset=True))


@router.delete("/qualifications/{qid}", status_code=204)
def delete_qualification(
    qid: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    service.delete_qualification(db, user, qid)
    return Response(status_code=204)


@router.put("/skills", response_model=list[SkillOut])
def replace_skills(
    body: SkillsReplace, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service.replace_skills(db, user, [s.model_dump() for s in body.skills])


@router.get("/experiences", response_model=list[ExperienceOut])
def list_experiences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.get_or_create_profile(db, user).experiences


@router.post("/experiences", response_model=ExperienceOut, status_code=201)
def add_experience(
    body: ExperienceCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service.add_experience(db, user, body.model_dump())


@router.delete("/experiences/{eid}", status_code=204)
def delete_experience(
    eid: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    service.delete_experience(db, user, eid)
    return Response(status_code=204)