import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.auth.models import User
from app.modules.eligibility import service
from app.modules.eligibility.rules import RuleTreeInvalid, validate_tree
from app.modules.eligibility.schemas import (
    EligibilityOut,
    EligibilityPage,
    EligibilityStatus,
    RequirementsPut,
)
from app.modules.opportunities import service as opportunities_service
from app.modules.opportunities.schemas import OpportunityOut

router = APIRouter(tags=["eligibility"])
admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_role("data_manager", "admin"))],
)


def _to_out(row) -> dict:
    return {
        "opportunity": row.opportunity,
        "status": row.status,
        "explanation": row.explanation,
        "requirements": row.details["requirements"],
        "counts": row.details["counts"],
        "data_freshness": row.details["data_freshness"],
        "ruleset_version": row.ruleset_version,
        "engine_version": row.engine_version,
        "evaluated_at": row.evaluated_at,
    }


@admin_router.put("/opportunities/{opp_id}/requirements", response_model=OpportunityOut)
def put_requirements(opp_id: uuid.UUID, body: RequirementsPut, db: Session = Depends(get_db)):
    tree = None
    if body.requirements is not None:
        try:
            tree = validate_tree(body.requirements)
        except RuleTreeInvalid as exc:
            raise AppError(422, "invalid_requirements", "The requirement tree is invalid", exc.errors)
    return opportunities_service.set_requirements(db, opp_id, tree)


@router.post("/opportunities/{opp_id}/eligibility", response_model=EligibilityOut)
def evaluate(
    opp_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _to_out(service.evaluate_for_user(db, user, opp_id))


@router.get("/eligibility", response_model=EligibilityPage)
def my_results(
    status: EligibilityStatus | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows, total = service.list_results(db, user, status, limit, offset)
    return {"items": [_to_out(r) for r in rows], "total": total, "limit": limit, "offset": offset}