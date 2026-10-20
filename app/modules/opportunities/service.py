import uuid
from datetime import datetime, timezone

from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.opportunities import models as m
from app.modules.opportunities import schemas as s

DETAIL_ORM = {
    "admission": m.AdmissionDetails,
    "scholarship": m.ScholarshipDetails,
    "internship": m.InternshipDetails,
}
NOT_NULLABLE = ("title", "organization_id", "work_mode", "status")
OFFICIAL_SOURCE_TYPES = ("official_website", "official_portal")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ---------- organizations ----------

def create_organization(db: Session, data: dict) -> m.Organization:
    org = m.Organization(**data)
    db.add(org)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "organization_exists", "This organization already exists in that country")
    return org


def list_organizations(db: Session, q: str | None, limit: int, offset: int):
    stmt = select(m.Organization)
    if q:
        stmt = stmt.where(m.Organization.name.ilike(_like(q), escape="\\"))
    return db.scalars(stmt.order_by(m.Organization.name).limit(limit).offset(offset)).all()


# ---------- sources ----------

def create_source(db: Session, data: dict) -> m.Source:
    src = m.Source(**data, is_official=data["type"] in OFFICIAL_SOURCE_TYPES)
    db.add(src)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "source_exists", "A source with this URL already exists")
    return src


def list_sources(db: Session, limit: int, offset: int):
    stmt = select(m.Source).order_by(m.Source.name).limit(limit).offset(offset)
    return db.scalars(stmt).all()


# ---------- opportunities ----------

def _load(db: Session, opp_id: uuid.UUID) -> m.Opportunity | None:
    stmt = (
        select(m.Opportunity)
        .where(m.Opportunity.id == opp_id)
        .execution_options(populate_existing=True)
    )
    return db.scalar(stmt)


def _require_org(db: Session, org_id: uuid.UUID) -> None:
    if db.get(m.Organization, org_id) is None:
        raise AppError(422, "invalid_reference", "Organization not found")


def _require_source(db: Session, source_id: uuid.UUID) -> None:
    if db.get(m.Source, source_id) is None:
        raise AppError(422, "invalid_reference", "Source not found")


def create_opportunity(db: Session, body) -> m.Opportunity:
    _require_org(db, body.organization_id)
    if body.source_id is not None:
        _require_source(db, body.source_id)

    category = body.category
    common = body.model_dump(exclude=set(s.DETAIL_MODELS))
    opp = m.Opportunity(**common)
    detail_data = getattr(body, category).model_dump()
    setattr(opp, category, DETAIL_ORM[category](**detail_data))
    db.add(opp)
    db.commit()
    return _load(db, opp.id)


def update_opportunity(db: Session, opp_id: uuid.UUID, patch: dict) -> m.Opportunity:
    opp = _load(db, opp_id)
    if opp is None:
        raise AppError(404, "not_found", "Opportunity not found")

    details_patch = patch.pop("details", None)
    for key in NOT_NULLABLE:
        if key in patch and patch[key] is None:
            raise AppError(422, "validation_error", f"{key} cannot be null")
    if patch.get("organization_id") is not None:
        _require_org(db, patch["organization_id"])
    if patch.get("source_id") is not None:
        _require_source(db, patch["source_id"])

    for key, value in patch.items():
        setattr(opp, key, value)
    if opp.opens_at and opp.deadline_at and opp.deadline_at <= opp.opens_at:
        db.rollback()
        raise AppError(422, "validation_error", "deadline_at must be after opens_at")

    if details_patch is not None:
        detail = getattr(opp, opp.category)
        model = s.DETAIL_MODELS[opp.category]
        merged = {name: getattr(detail, name) for name in model.model_fields}
        merged.update(details_patch)
        try:
            validated = model.model_validate(merged)
        except ValidationError as exc:
            db.rollback()
            raise AppError(
                422,
                "validation_error",
                "Invalid details",
                jsonable_encoder(
                    exc.errors(include_url=False, include_context=False, include_input=False)
                ),
            )
        for name, value in validated.model_dump().items():
            setattr(detail, name, value)

    # Changing the content of verified data means it needs checking again.
    changed_content = details_patch is not None or bool(set(patch) - {"status"})
    if changed_content and opp.data_quality == "verified":
        opp.data_quality = "needs_review"

    db.commit()
    return _load(db, opp.id)


def verify_opportunity(db: Session, opp_id: uuid.UUID) -> m.Opportunity:
    opp = _load(db, opp_id)
    if opp is None:
        raise AppError(404, "not_found", "Opportunity not found")
    if opp.source_id is None:
        raise AppError(
            422, "source_required", "Attach a source before marking an opportunity as verified"
        )
    opp.data_quality = "verified"
    opp.last_verified_at = _now()
    db.commit()
    return _load(db, opp.id)


def get_opportunity(db: Session, opp_id: uuid.UUID, *, public: bool) -> m.Opportunity:
    opp = db.scalar(select(m.Opportunity).where(m.Opportunity.id == opp_id))
    hidden = opp is None or (public and opp.status not in ("open", "closed"))
    if hidden:
        raise AppError(404, "not_found", "Opportunity not found")
    return opp


def list_opportunities(
    db: Session,
    *,
    public: bool,
    category: str | None = None,
    country: str | None = None,
    q: str | None = None,
    status: str | None = None,
    work_mode: str | None = None,
    deadline_before: datetime | None = None,
    organization_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
):
    O, Org = m.Opportunity, m.Organization
    conds = []
    if public:
        conds += [O.status == "open", or_(O.deadline_at.is_(None), O.deadline_at > _now())]
    elif status:
        conds.append(O.status == status)
    if category:
        conds.append(O.category == category)
    if country:
        conds.append(O.country == country.upper())
    if work_mode:
        conds.append(O.work_mode == work_mode)
    if organization_id:
        conds.append(O.organization_id == organization_id)
    if deadline_before:
        conds.append(O.deadline_at <= deadline_before)
    if q and q.strip():
        pattern = _like(q.strip())
        conds.append(or_(O.title.ilike(pattern, escape="\\"), Org.name.ilike(pattern, escape="\\")))

    join_on = O.organization_id == Org.id
    total = db.scalar(select(func.count()).select_from(O).join(Org, join_on).where(*conds))
    stmt = (
        select(O)
        .join(Org, join_on)
        .where(*conds)
        .order_by(O.deadline_at.asc().nulls_last(), O.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = db.scalars(stmt).unique().all()
    return items, total