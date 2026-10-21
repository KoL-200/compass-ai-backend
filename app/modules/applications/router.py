import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.applications import service
from app.modules.applications.schemas import (
    ApplicationCreate,
    ApplicationDetail,
    ApplicationPage,
    ApplicationStatus,
    ApplicationUpdate,
    ChecklistCreate,
    ChecklistItemOut,
    ChecklistUpdate,
    EventOut,
)
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("", response_model=ApplicationDetail, status_code=201)
def create_application(
    body: ApplicationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service.create_application(db, user, body.opportunity_id)


@router.get("", response_model=ApplicationPage)
def list_applications(
    status: ApplicationStatus | None = None,
    active: bool | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items, total = service.list_applications(
        db, user, status=status, active=active, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/{app_id}", response_model=ApplicationDetail)
def get_application(
    app_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service.get_application(db, user, app_id)


@router.patch("/{app_id}", response_model=ApplicationDetail)
def update_application(
    app_id: uuid.UUID,
    body: ApplicationUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.update_application(db, user, app_id, body.model_dump(exclude_unset=True))


@router.delete("/{app_id}", status_code=204)
def delete_application(
    app_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    service.delete_application(db, user, app_id)
    return Response(status_code=204)


@router.get("/{app_id}/timeline", response_model=list[EventOut])
def get_timeline(
    app_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service.timeline(db, user, app_id)


@router.post("/{app_id}/checklist", response_model=ChecklistItemOut, status_code=201)
def add_checklist_item(
    app_id: uuid.UUID,
    body: ChecklistCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.add_checklist_item(db, user, app_id, body.label)


@router.patch("/{app_id}/checklist/{item_id}", response_model=ChecklistItemOut)
def update_checklist_item(
    app_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ChecklistUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.update_checklist_item(db, user, app_id, item_id, body.model_dump(exclude_unset=True))


@router.delete("/{app_id}/checklist/{item_id}", status_code=204)
def delete_checklist_item(
    app_id: uuid.UUID,
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service.delete_checklist_item(db, user, app_id, item_id)
    return Response(status_code=204)