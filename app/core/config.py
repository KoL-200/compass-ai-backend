from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = Field(min_length=32)
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    log_level: str = "INFO"
    job_secret: str = ""
    job_backend: str = "inline"
    rate_limit_enabled: bool = True
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    public_base_url: str = "http://localhost:8000"
    frontend_callback_url: str = "http://localhost:8000/oauth-placeholder"
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()