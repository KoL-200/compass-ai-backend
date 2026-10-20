import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://compass:compass@localhost:5432/compass"
)
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")