import os

# Force the test database, even if .env points at the dev one.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://compass:compass@db:5432/compass_test"
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://redis:6379/1")
os.environ["RATE_LIMIT_ENABLED"] = "false"  # only the tests that check limits turn it on
os.environ["ENV"] = "development"
os.environ["EMAIL_BACKEND"] = "memory"
os.environ["JOB_SECRET"] = "test-job-secret-not-for-production"
os.environ["AI_RETRY_BACKOFF_SECONDS"] = "0"
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-use-1234")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import Base, engine
from app.core.ratelimit import get_redis
import app.modules.auth.models
import app.modules.profiles.models 
import app.modules.opportunities.models 
import app.modules.matching.models
import app.modules.applications.models
import app.modules.notifications.models
import app.modules.goals.models  # noqa: F401
import app.modules.ai.models  # noqa: F401
import app.modules.strategy.models  # noqa: F401
from app.main import app

from app.core import mailer


@pytest.fixture(autouse=True)
def _clean_outbox():
    mailer.OUTBOX.clear()
    yield


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean_db():
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        conn.execute(
            text("INSERT INTO roles (name) VALUES ('user'),('admin'),('data_manager'),('reviewer')")
        )
    yield

@pytest.fixture(autouse=True)
def _clean_redis():
    try:
        get_redis().flushdb()
    except Exception:
        pass
    yield

@pytest.fixture
def client():
    return TestClient(app)