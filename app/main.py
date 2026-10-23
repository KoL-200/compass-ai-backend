from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import register_error_handlers
from app.modules.auth.router import router as auth_router
from app.modules.auth.router import users_router
from app.modules.eligibility.router import admin_router as eligibility_admin_router
from app.modules.eligibility.router import router as eligibility_router
from app.modules.opportunities.router import admin_router as opportunities_admin_router
from app.modules.opportunities.router import router as opportunities_router
from app.modules.profiles.router import router as profiles_router
from app.modules.matching.router import router as matching_router
from app.modules.applications.router import router as applications_router
from app.modules.notifications.router import internal_router as jobs_router
from app.modules.notifications.router import router as notifications_router
from app.modules.auth.router import placeholder_router

app = FastAPI(title="Compass AI", version="0.1.0")
register_error_handlers(app)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(profiles_router, prefix="/api/v1")
app.include_router(opportunities_router, prefix="/api/v1")
app.include_router(opportunities_admin_router, prefix="/api/v1")
app.include_router(eligibility_router, prefix="/api/v1")
app.include_router(eligibility_admin_router, prefix="/api/v1")
app.include_router(matching_router, prefix="/api/v1")
app.include_router(applications_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(jobs_router)
app.include_router(placeholder_router)


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready"}