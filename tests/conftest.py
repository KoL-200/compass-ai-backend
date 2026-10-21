import os

# Force the test database, even if .env points at the dev one.
os.environ["DATABASE_URL"] = "postgresql+psycopg://compass:compass@db:5432/compass_test"
os.environ["JOB_SECRET"] = "test-job-secret-not-for-production"
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-use-1234")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import Base, engine
import app.modules.auth.models
import app.modules.profiles.models 
import app.modules.opportunities.models 
import app.modules.matching.models
import app.modules.applications.models
import app.modules.notifications.models
from app.main import app


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


@pytest.fixture
def client():
    return TestClient(app)