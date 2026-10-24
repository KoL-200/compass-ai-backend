import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.ratelimit import enforce
from app.modules.ai import service
from app.modules.ai.orchestrator import execute_run
from app.modules.ai.schemas import (
    ApplyOut, ApplyRequest, ExplainRequest, ExtractRequest, GuidanceRequest, RunCreated, RunOut, RunPage, UsageOut,
    ChecklistApplyOut, ChecklistApplyRequest, CompareRequest, DiscoverRequest, ProgressRequest, ReviewRequest
)
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.opportunities import service as opportunities_service
from app.modules.applications import service as applications_service

router = APIRouter(prefix="/ai", tags=["ai"])

def _start(db: Session, user: User, background: BackgroundTasks, task: str, meta: dict, payload: dict):
    enforce("ai-start", str(user.id), 10, 60)
    run = service.create_run(db, user, task, meta)
    background.add_task(execute_run, run.id, payload)
    return {"run_id": run.id, "status": run.status}

@router.post("/profile/extract", response_model=RunCreated, status_code=202)
def extract_profile(
    body: ExtractRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce("ai-start", str(user.id), 10, 60)
    # Only the length is stored. The text itself exists in memory for the duration of the run.
    run = service.create_run(db, user, "profile_extract", {"chars": len(body.text)})
    background.add_task(execute_run, run.id, {"text": body.text})
    return {"run_id": run.id, "status": run.status}


@router.post("/eligibility/explain", response_model=RunCreated, status_code=202)
def explain_eligibility(
    body: ExplainRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce("ai-start", str(user.id), 10, 60)
    opportunities_service.get_opportunity(db, body.opportunity_id, public=True)  # 404 early for hidden ones
    run = service.create_run(db, user, "eligibility_explain", {"opportunity_id": str(body.opportunity_id)})
    background.add_task(execute_run, run.id, {"opportunity_id": str(body.opportunity_id)})
    return {"run_id": run.id, "status": run.status}


@router.post("/guidance", response_model=RunCreated, status_code=202)
def generate_guidance(
    body: GuidanceRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce("ai-start", str(user.id), 10, 60)
    opportunities_service.get_opportunity(db, body.opportunity_id, public=True)
    payload = {"opportunity_id": str(body.opportunity_id), "force": body.force}
    run = service.create_run(db, user, "guidance", payload)
    background.add_task(execute_run, run.id, payload)
    return {"run_id": run.id, "status": run.status}


@router.get("/runs", response_model=RunPage)
def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows, total = service.list_runs(db, user, limit, offset)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.get_run(db, user, run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.cancel_run(db, user, run_id)


@router.post("/runs/{run_id}/apply", response_model=ApplyOut)
def apply_run(
    run_id: uuid.UUID,
    body: ApplyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.apply_profile_proposal(db, user, run_id, body.fields)


@router.get("/usage", response_model=UsageOut)
def usage(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.usage(db, user)

@router.post("/discover", response_model=RunCreated, status_code=202)
def discover(body: DiscoverRequest, background: BackgroundTasks,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = body.model_dump()
    return _start(db, user, background, "opportunity_discover", payload, payload)


@router.post("/compare", response_model=RunCreated, status_code=202)
def compare(body: CompareRequest, background: BackgroundTasks,
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for opp_id in body.opportunity_ids:
        opportunities_service.get_opportunity(db, opp_id, public=True)  # 404 early for hidden ones
    payload = {"opportunity_ids": [str(i) for i in body.opportunity_ids]}
    return _start(db, user, background, "opportunity_compare", payload, payload)


@router.post("/applications/{application_id}/checklist", response_model=RunCreated, status_code=202)
def propose_checklist(application_id: uuid.UUID, background: BackgroundTasks,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    applications_service.get_application(db, user, application_id)  # other people's are 404
    payload = {"application_id": str(application_id)}
    return _start(db, user, background, "application_checklist", payload, payload)


@router.post("/applications/{application_id}/review", response_model=RunCreated, status_code=202)
def review_draft(application_id: uuid.UUID, body: ReviewRequest, background: BackgroundTasks,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    applications_service.get_application(db, user, application_id)
    # Only the length is stored. The draft exists in memory for the duration of the run.
    meta = {"application_id": str(application_id), "kind": body.kind, "chars": len(body.text)}
    payload = {"application_id": str(application_id), "kind": body.kind, "text": body.text}
    return _start(db, user, background, "draft_review", meta, payload)


@router.post("/progress-summary", response_model=RunCreated, status_code=202)
def progress_summary(body: ProgressRequest, background: BackgroundTasks,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payload = body.model_dump()
    return _start(db, user, background, "progress_summary", payload, payload)


@router.post("/runs/{run_id}/apply-checklist", response_model=ChecklistApplyOut)
def apply_checklist(run_id: uuid.UUID, body: ChecklistApplyRequest,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.apply_checklist(db, user, run_id, body.items)