import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.auth.models import User
from app.modules.profiles.models import (
    Profile,
    ProfileSkill,
    Qualification,
    Skill,
    WorkExperience,
)

LIST_FIELDS = {"career_interests", "preferred_locations"}
REQUIRED_FOR_MATCHING = ("country", "nationality", "education_level", "field_of_study")


def missing_fields(profile: Profile) -> list[str]:
    return [f for f in REQUIRED_FOR_MATCHING if getattr(profile, f) in (None, "")]


def get_or_create_profile(db: Session, user: User) -> Profile:
    profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    if profile is None:
        profile = Profile(user_id=user.id)
        db.add(profile)
        try:
            db.commit()
        except IntegrityError:  # created by a parallel request
            db.rollback()
            profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
    return profile


def update_profile(db: Session, user: User, data: dict) -> Profile:
    profile = get_or_create_profile(db, user)
    for key, value in data.items():
        if key in LIST_FIELDS and value is None:
            value = []
        setattr(profile, key, value)
    db.commit()
    return profile


# ---- qualifications ----

def _get_qualification(db: Session, profile: Profile, qid: uuid.UUID) -> Qualification:
    q = db.scalar(
        select(Qualification).where(Qualification.id == qid, Qualification.profile_id == profile.id)
    )
    if q is None:
        raise AppError(404, "not_found", "Qualification not found")
    return q


def add_qualification(db: Session, user: User, data: dict) -> Qualification:
    profile = get_or_create_profile(db, user)
    q = Qualification(**data)
    profile.qualifications.append(q)
    db.commit()
    return q


def update_qualification(db: Session, user: User, qid: uuid.UUID, data: dict) -> Qualification:
    profile = get_or_create_profile(db, user)
    q = _get_qualification(db, profile, qid)
    for key, value in data.items():
        if value is None and key in ("type", "subject_results"):
            continue  # these cannot be cleared
        setattr(q, key, value)
    if q.overall_score is not None and q.score_scale is not None and q.overall_score > q.score_scale:
        db.rollback()
        raise AppError(422, "validation_error", "overall_score cannot exceed score_scale")
    db.commit()
    return q


def delete_qualification(db: Session, user: User, qid: uuid.UUID) -> None:
    profile = get_or_create_profile(db, user)
    q = _get_qualification(db, profile, qid)
    profile.qualifications.remove(q)  # delete-orphan removes the row
    db.commit()


# ---- skills ----

def replace_skills(db: Session, user: User, items: list[dict]) -> list[ProfileSkill]:
    profile = get_or_create_profile(db, user)

    wanted: dict[str, str | None] = {}  # last duplicate wins
    for item in items:
        wanted[item["name"].strip().lower()] = item.get("level")

    existing = {
        s.name: s for s in db.scalars(select(Skill).where(Skill.name.in_(list(wanted)))).all()
    }
    for name in wanted:
        if name not in existing:
            skill = Skill(name=name)
            db.add(skill)
            existing[name] = skill
    db.flush()

    for old in list(profile.skills):
        profile.skills.remove(old)
    db.flush()
    for name, level in wanted.items():
        profile.skills.append(ProfileSkill(skill=existing[name], level=level))
    db.commit()
    return profile.skills


# ---- work experience ----

def add_experience(db: Session, user: User, data: dict) -> WorkExperience:
    profile = get_or_create_profile(db, user)
    exp = WorkExperience(**data)
    profile.experiences.append(exp)
    db.commit()
    return exp


def delete_experience(db: Session, user: User, eid: uuid.UUID) -> None:
    profile = get_or_create_profile(db, user)
    exp = db.scalar(
        select(WorkExperience).where(
            WorkExperience.id == eid, WorkExperience.profile_id == profile.id
        )
    )
    if exp is None:
        raise AppError(404, "not_found", "Work experience not found")
    profile.experiences.remove(exp)
    db.commit()