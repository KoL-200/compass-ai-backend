import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.ratelimit import enforce
from app.modules.ai import service as ai_service
from app.modules.ai.orchestrator import execute_run
from app.modules.ai.schemas import RunCreated
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.opportunities import service as opportunities_service
from app.modules.strategy import service
from app.modules.strategy.schemas import (
    GenerateRequest, StrategyOut, StrategyPage, StrategyStatus, StrategyStatusUpdate, TaskCreate, TaskOut,
    TaskUpdate,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _start(db: Session, user: User, background: BackgroundTasks, opportunity_id: uuid.UUID, force: bool):
    enforce("ai-start", str(user.id), 10, 60)
    opportunities_service.get_opportunity(db, opportunity_id, public=True)  # 404 early for hidden ones
    payload = {"opportunity_id": str(opportunity_id), "force": force}
    run = ai_service.create_run(db, user, "strategy_generate", payload)
    background.add_task(execute_run, run.id, payload)
    return {"run_id": run.id, "status": run.status}


@router.post("/generate", response_model=RunCreated, status_code=202)
def generate(
    body: GenerateRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _start(db, user, background, body.opportunity_id, body.force)


@router.get("", response_model=StrategyPage)
def list_strategies(
    opportunity_id: uuid.UUID | None = None,
    status: StrategyStatus | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows, total = service.list_strategies(
        db, user, opportunity_id=opportunity_id, status=status, limit=limit, offset=offset
    )
    items = [
        {
            "id": s.id, "opportunity": s.opportunity, "status": s.status, "source": s.source,
            "summary": s.summary, "created_at": s.created_at, "tasks_total": len(s.tasks),
            "tasks_done": sum(t.status == "done" for t in s.tasks),
        }
        for s in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/{strategy_id}", response_model=StrategyOut)
def get_strategy(strategy_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.get_owned(db, user, strategy_id)


@router.patch("/{strategy_id}", response_model=StrategyOut)
def update_status(
    strategy_id: uuid.UUID,
    body: StrategyStatusUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.set_status(db, user, strategy_id, body.status)


@router.post("/{strategy_id}/regenerate", response_model=RunCreated, status_code=202)
def regenerate(
    strategy_id: uuid.UUID,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    strategy = service.get_owned(db, user, strategy_id)
    return _start(db, user, background, strategy.opportunity_id, False)


@router.post("/{strategy_id}/tasks", response_model=TaskOut, status_code=201)
def add_task(
    strategy_id: uuid.UUID, body: TaskCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service.add_task(db, user, strategy_id, body.model_dump())


@router.patch("/{strategy_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(
    strategy_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.update_task(db, user, strategy_id, task_id, body.model_dump(exclude_unset=True))


@router.delete("/{strategy_id}/tasks/{task_id}", status_code=204)
def delete_task(
    strategy_id: uuid.UUID, task_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    service.delete_task(db, user, strategy_id, task_id)
    return Response(status_code=204)