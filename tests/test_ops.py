import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.logging import JsonFormatter, request_id_var
from app.core.startup import parse_origins, production_checks, validate_startup
from app.main import create_app

JOB_SECRET = "test-job-secret-not-for-production"


def prod(**over):
    values = dict(
        env="production", jwt_secret="k" * 48, job_secret="j" * 24,
        database_url="postgresql+psycopg://app:pw@db.example.com/app",
        redis_url="rediss://default:pw@redis.example.com:6379",
        public_base_url="https://api.example.com", frontend_callback_url="https://app.example.com/auth/callback",
        cors_origins="https://app.example.com", google_client_id="", github_client_id="",
        email_backend="resend", resend_api_key="re_test", smtp_host="", rate_limit_enabled=True,
        docs_enabled=False, allow_oauth_placeholder=False,
    )
    values.update(over)
    return get_settings().model_copy(update=values)


# ---------- startup checks ----------

def test_a_correct_production_configuration_is_clean():
    assert production_checks(prod()) == ([], [])
    assert validate_startup(prod()) == []


@pytest.mark.parametrize("override,fragment", [
    ({"jwt_secret": "short"}, "JWT_SECRET"),
    ({"jwt_secret": "change-me-" + "x" * 40}, "JWT_SECRET"),
    ({"job_secret": ""}, "JOB_SECRET"),
    ({"database_url": "postgresql+psycopg://compass:compass@db:5432/compass"}, "DATABASE_URL"),
    ({"public_base_url": "http://api.example.com"}, "PUBLIC_BASE_URL"),
    ({"cors_origins": "https://a.example.com, *"}, "CORS_ORIGINS"),
    ({"rate_limit_enabled": False}, "RATE_LIMIT_ENABLED"),
    ({"email_backend": "resend", "resend_api_key": ""}, "RESEND_API_KEY"),
    ({"email_backend": "smtp", "smtp_host": ""}, "SMTP_HOST"),
    ({"google_client_id": "gid", "frontend_callback_url": "http://localhost:3000/cb"}, "FRONTEND_CALLBACK_URL"),
])
def test_unsafe_production_settings_block_startup(override, fragment):
    problems, _ = production_checks(prod(**override))
    assert any(fragment in p for p in problems), problems
    with pytest.raises(RuntimeError, match=fragment):
        validate_startup(prod(**override))


def test_warnings_do_not_block_startup():
    s = prod(email_backend="console", redis_url="redis://localhost:6379/0", docs_enabled=True,
             google_client_id="gid", allow_oauth_placeholder=True,
             frontend_callback_url="https://api.example.com/oauth-placeholder")
    problems, warnings = production_checks(s)
    assert problems == []
    assert {w.split(" ")[0] for w in warnings} == {"EMAIL_BACKEND", "REDIS_URL", "DOCS_ENABLED", "ALLOW_OAUTH_PLACEHOLDER"}
    assert len(validate_startup(s)) == 4


def test_nothing_is_checked_outside_production():
    assert validate_startup(prod(env="development", jwt_secret="x")) == []


def test_origins_are_parsed_and_database_urls_normalised():
    assert parse_origins(" https://a.com/ , ,https://b.com") == ["https://a.com", "https://b.com"]
    assert parse_origins("") == []
    s = get_settings().model_validate({"database_url": "postgres://u:p@host/db", "jwt_secret": "x" * 40})
    assert s.database_url == "postgresql+psycopg://u:p@host/db"
    s = get_settings().model_validate({"database_url": "postgresql://u:p@host/db?sslmode=require", "jwt_secret": "x" * 40})
    assert s.database_url == "postgresql+psycopg://u:p@host/db?sslmode=require"


# ---------- headers, request ids, CORS, docs ----------

def test_responses_carry_security_headers_and_a_request_id():
    client = TestClient(create_app())
    r = client.get("/health/live")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer" and len(r.headers["x-request-id"]) == 32
    assert "content-security-policy" not in r.headers and "strict-transport-security" not in r.headers

    api = client.get("/api/v1/users/me")  # a normal API error carries the same protections
    assert api.status_code == 401 and api.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in api.headers["content-security-policy"]

    docs = client.get("/docs")  # the Swagger page needs scripts, so it gets no strict CSP
    assert docs.status_code == 200 and "content-security-policy" not in docs.headers


def test_request_ids_are_echoed_only_when_they_look_safe():
    client = TestClient(create_app())
    good = client.get("/health/live", headers={"X-Request-ID": "req-abcdef123"})
    assert good.headers["x-request-id"] == "req-abcdef123"
    bad = client.get("/health/live", headers={"X-Request-ID": "has spaces in it!"})
    assert bad.headers["x-request-id"] != "has spaces in it!" and len(bad.headers["x-request-id"]) == 32


def test_hsts_is_only_sent_in_production(monkeypatch):
    monkeypatch.setattr(get_settings(), "env", "production")
    r = TestClient(create_app()).get("/health/live")
    assert r.headers["strict-transport-security"] == "max-age=31536000"


def test_cors_allows_only_listed_origins(monkeypatch):
    monkeypatch.setattr(get_settings(), "cors_origins", "https://app.example.com")
    client = TestClient(create_app())
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    ok = client.options("/api/v1/auth/login", headers={"Origin": "https://app.example.com", **preflight})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "Authorization" in ok.headers["access-control-allow-headers"]
    other = client.options("/api/v1/auth/login", headers={"Origin": "https://evil.example.com", **preflight})
    assert "access-control-allow-origin" not in other.headers
    simple = client.get("/health/live", headers={"Origin": "https://app.example.com"})
    assert simple.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "x-request-id" in simple.headers["access-control-expose-headers"].lower()

    monkeypatch.setattr(get_settings(), "cors_origins", "")  # no CORS at all by default
    plain = TestClient(create_app()).get("/health/live", headers={"Origin": "https://app.example.com"})
    assert "access-control-allow-origin" not in plain.headers


def test_documentation_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "docs_enabled", False)
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404
    assert client.get("/health/live").status_code == 200


# ---------- errors and logging ----------

def test_unexpected_errors_return_a_generic_500_with_a_request_id():
    app = create_app()

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret detail: password=hunter2")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/boom", headers={"X-Request-ID": "req-abcdef123"})
    error = r.json()["error"]
    assert r.status_code == 500 and error["code"] == "internal_error" and error["request_id"] == "req-abcdef123"
    assert "hunter2" not in r.text


def test_requests_are_logged_without_query_strings_or_health_noise(caplog):
    caplog.set_level(logging.INFO, logger="app.request")
    client = TestClient(create_app())
    client.get("/api/v1/users/me?token=SECRETTOKEN")
    client.get("/health/live")
    records = [r for r in caplog.records if r.name == "app.request"]
    assert [(r.method, r.path, r.status) for r in records] == [("GET", "/api/v1/users/me", 401)]
    line = JsonFormatter().format(records[0])
    assert "SECRETTOKEN" not in line and json.loads(line)["duration_ms"] >= 0


def test_json_log_lines_include_the_request_id_and_exceptions():
    request_id_var.set("req-abcdef123")
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord("app", logging.ERROR, __file__, 1, "failed %s", ("again",), sys.exc_info())
    data = json.loads(JsonFormatter().format(record))
    assert data["message"] == "failed again" and data["level"] == "ERROR" and data["request_id"] == "req-abcdef123"
    assert "ValueError: boom" in data["exception"]


# ---------- the daily job ----------

def test_the_daily_job_runs_every_step_and_reports_failures(client, monkeypatch):
    from app.modules.notifications import jobs

    def broken():
        raise RuntimeError("database exploded")

    monkeypatch.setattr(jobs, "run_scan", broken)
    r = client.post("/internal/jobs/daily", headers={"X-Job-Secret": JOB_SECRET})
    assert r.status_code == 500 and r.json()["error"]["code"] == "job_failed"
    assert r.json()["error"]["details"] == ["scan-deadlines"] and "exploded" not in r.text