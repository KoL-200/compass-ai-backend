"""Checks that stop an unsafe production configuration from starting."""
WEAK_MARKERS = ("change-me", "changeme", "test-secret", "secret123")


def parse_origins(raw: str) -> list[str]:
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def production_checks(s) -> tuple[list[str], list[str]]:
    """Returns (problems that must block startup, warnings worth logging)."""
    problems: list[str] = []
    warnings: list[str] = []

    if len(s.jwt_secret) < 32 or any(m in s.jwt_secret.lower() for m in WEAK_MARKERS):
        problems.append("JWT_SECRET must be at least 32 random characters and must not be an example value")
    if len(s.job_secret) < 16:
        problems.append("JOB_SECRET must be at least 16 characters (the daily jobs cannot run without it)")
    if "compass:compass@" in s.database_url:
        problems.append("DATABASE_URL uses the default development credentials")
    if not s.rate_limit_enabled:
        problems.append("RATE_LIMIT_ENABLED must be true in production")
    if s.public_base_url.startswith("http://"):
        problems.append("PUBLIC_BASE_URL must use https")
    if "*" in parse_origins(s.cors_origins):
        problems.append("CORS_ORIGINS must list exact origins, not *")
    if s.email_backend == "resend" and not s.resend_api_key:
        problems.append("RESEND_API_KEY is not set")
    if s.email_backend == "smtp" and not s.smtp_host:
        problems.append("SMTP_HOST is not set")

    oauth_on = bool(s.google_client_id or s.github_client_id)
    callback = s.frontend_callback_url
    if oauth_on and not s.allow_oauth_placeholder and (
        "localhost" in callback or callback.endswith("/oauth-placeholder")
    ):
        problems.append(
            "FRONTEND_CALLBACK_URL must point at your real frontend when Google or GitHub sign-in is enabled"
        )

    if s.email_backend == "console":
        warnings.append("EMAIL_BACKEND is 'console': verification and reset emails will not be delivered")
    if "localhost" in s.redis_url or "redis://redis" in s.redis_url:
        warnings.append("REDIS_URL looks local: rate limiting will fail open if Redis is unreachable")
    if s.docs_enabled:
        warnings.append("DOCS_ENABLED is true: the API documentation is public")
    if s.allow_oauth_placeholder:
        warnings.append("ALLOW_OAUTH_PLACEHOLDER is true: for demos only. Turn it off when a frontend exists")
    return problems, warnings


def validate_startup(s) -> list[str]:
    """Returns warnings. Raises RuntimeError if the production configuration is unsafe."""
    if s.env != "production":
        return []
    problems, warnings = production_checks(s)
    if problems:
        raise RuntimeError("Unsafe production configuration:\n- " + "\n- ".join(problems))
    return warnings