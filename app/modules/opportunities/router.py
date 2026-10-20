import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.auth.models import User
from app.modules.opportunities import service
from app.modules.opportunities.schemas import (
    Category,
    OpportunityCreate,
    OpportunityOut,
    OpportunityPage,
    OpportunityUpdate,
    OrganizationCreate,
    OrganizationOut,
    SourceCreate,
    SourceOut,
    Status,
    WorkMode,
)

router = APIRouter(tags=["opportunities"])
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_role("data_manager", "admin"))],
)


# ---------- public (any logged-in user) ----------

@router.get("/opportunities", response_model=OpportunityPage)
def list_opportunities(
    category: Category | None = None,
    country: str | None = Query(default=None, min_length=2, max_length=2),
    q: str | None = Query(default=None, max_length=100),
    work_mode: WorkMode | None = None,
    deadline_before: datetime | None = None,
    organization_id: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items, total = service.list_opportunities(
        db,
        public=True,
        category=category,
        country=country,
        q=q,
        work_mode=work_mode,
        deadline_before=deadline_before,
        organization_id=organization_id,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/opportunities/{opp_id}", response_model=OpportunityOut)
def get_opportunity(
    opp_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service.get_opportunity(db, opp_id, public=True)


# ---------- admin (data_manager or admin) ----------

@admin_router.post("/organizations", response_model=OrganizationOut, status_code=201)
def create_organization(body: OrganizationCreate, db: Session = Depends(get_db)):
    return service.create_organization(db, body.model_dump())


@admin_router.get("/organizations", response_model=list[OrganizationOut])
def list_organizations(
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return service.list_organizations(db, q, limit, offset)


@admin_router.post("/sources", response_model=SourceOut, status_code=201)
def create_source(body: SourceCreate, db: Session = Depends(get_db)):
    return service.create_source(db, body.model_dump())


@admin_router.get("/sources", response_model=list[SourceOut])
def list_sources(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return service.list_sources(db, limit, offset)


@admin_router.post("/opportunities", response_model=OpportunityOut, status_code=201)
def create_opportunity(body: OpportunityCreate, db: Session = Depends(get_db)):
    return service.create_opportunity(db, body)


@admin_router.get("/opportunities", response_model=OpportunityPage)
def admin_list_opportunities(
    category: Category | None = None,
    status: Status | None = None,
    country: str | None = Query(default=None, min_length=2, max_length=2),
    q: str | None = Query(default=None, max_length=100),
    organization_id: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = service.list_opportunities(
        db,
        public=False,
        category=category,
        status=status,
        country=country,
        q=q,
        organization_id=organization_id,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@admin_router.get("/opportunities/{opp_id}", response_model=OpportunityOut)
def admin_get_opportunity(opp_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.get_opportunity(db, opp_id, public=False)


@admin_router.patch("/opportunities/{opp_id}", response_model=OpportunityOut)
def update_opportunity(opp_id: uuid.UUID, body: OpportunityUpdate, db: Session = Depends(get_db)):
    return service.update_opportunity(db, opp_id, body.model_dump(exclude_unset=True))


@admin_router.post("/opportunities/{opp_id}/verify", response_model=OpportunityOut)
def verify_opportunity(opp_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.verify_opportunity(db, opp_id)