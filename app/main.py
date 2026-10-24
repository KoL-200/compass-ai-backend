import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.startup import parse_origins, validate_startup
from app.modules.ai import jobs as ai_jobs  # noqa: F401  (registers the ai-cleanup job)
from app.modules.ai.router import router as ai_router
from app.modules.applications.router import router as applications_router
from app.modules.auth.router import placeholder_router, users_router
from app.modules.auth.router import router as auth_router
from app.modules.careers.router import router as careers_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.eligibility.router import admin_router as eligibility_admin_router
from app.modules.eligibility.router import router as eligibility_router
from app.modules.goals.router import router as goals_router
from app.modules.matching.router import router as matching_router
from app.modules.notifications.router import internal_router as jobs_router
from app.modules.notifications.router import router as notifications_router
from app.modules.opportunities.router import admin_router as opportunities_admin_router
from app.modules.opportunities.router import router as opportunities_router
from app.modules.profiles.router import router as profiles_router
from app.modules.strategy.router import router as strategy_router

log = logging.getLogger("app")
API = "/api/v1"

_settings = get_settings()
configure_logging(_settings.log_level, json_logs=_settings.env != "development")


@asynccontextmanager
async def lifespan(app: FastAPI):
    for warning in validate_startup(get_settings()):  # raises in production if the config is unsafe
        log.warning("Configuration warning: %s", warning)
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Compass AI",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if s.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if s.docs_enabled else None,
    )
    register_error_handlers(app)

    origins = parse_origins(s.cors_origins)
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
            allow_credentials=False,
            max_age=600,
        )
    app.add_middleware(RequestContextMiddleware)  # added last, so it is the outermost layer

    for router in (
        auth_router, users_router, profiles_router, opportunities_router, opportunities_admin_router,
        eligibility_router, eligibility_admin_router, matching_router, applications_router,
        notifications_router, careers_router, goals_router, dashboard_router, strategy_router, ai_router,
    ):
        app.include_router(router, prefix=API)
    app.include_router(jobs_router)  # internal: not part of the public API
    app.include_router(placeholder_router)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(db: Session = Depends(get_db)):
        db.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()