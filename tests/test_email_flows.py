import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select, update

from app.core import mailer
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.modules.auth.models import AuthEvent, EmailToken, Role, User
from tests.helpers import PASSWORD

B = "/api/v1"
NEW_PASSWORD = "another-long-pass-9"
WRONG = "wrong-password-1"


@pytest.fixture(autouse=True)
def frontend(monkeypatch):
    monkeypatch.setattr(get_settings(), "frontend_base_url", "http://localhost:3000")


# ---------- helpers ----------

def register(client, email="a@example.com"):
    return client.post(f"{B}/auth/register", json={"email": email, "password": PASSWORD})


def login(client, email="a@example.com", password=PASSWORD):
    return client.post(f"{B}/auth/login", json={"email": email, "password": password})


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def forgot(client, email="a@example.com"):
    return client.post(f"{B}/auth/forgot-password", json={"email": email})


def reset(client, token, password):
    return client.post(f"{B}/auth/reset-password", json={"token": token, "new_password": password})


def verify(client, token):
    return client.post(f"{B}/auth/verify-email", json={"token": token})


def token_from(mail):
    match = re.search(r"token=([\w-]+)", mail["body"])
    assert match, mail["body"]
    return match.group(1)


def expire_all_links():
    with SessionLocal() as db:
        db.execute(update(EmailToken).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()


def event_names():
    with SessionLocal() as db:
        return [e.event for e in db.scalars(select(AuthEvent).order_by(AuthEvent.created_at))]


# ---------- email verification ----------

def test_registration_sends_a_verification_email(client):
    r = register(client)
    assert r.status_code == 201 and r.json()["email_verified"] is False
    assert len(mailer.OUTBOX) == 1
    mail = mailer.OUTBOX[0]
    assert mail["to"] == "a@example.com" and "Confirm" in mail["subject"]
    assert "http://localhost:3000/verify-email?token=" in mail["body"]


def test_verifying_the_email(client):
    register(client)
    tokens = login(client).json()
    token = token_from(mailer.OUTBOX[0])

    assert verify(client, token).status_code == 204
    assert client.get(f"{B}/users/me", headers=bearer(tokens)).json()["email_verified"] is True
    assert "email_verified" in event_names()

    again = verify(client, token)  # a link works only once
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_or_expired_link"
    assert verify(client, "x" * 30).status_code == 400


def test_expired_links_are_refused(client):
    register(client)
    token = token_from(mailer.OUTBOX[0])
    expire_all_links()
    r = verify(client, token)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_or_expired_link"


def test_links_only_work_for_their_own_purpose(client):
    register(client)
    verify_token = token_from(mailer.OUTBOX[0])
    forgot(client)
    reset_token = token_from(mailer.OUTBOX[-1])
    assert reset(client, verify_token, NEW_PASSWORD).status_code == 400
    assert verify(client, reset_token).status_code == 400


def test_resend_verification_replaces_the_old_link(client):
    register(client)
    tokens = login(client).json()
    old = token_from(mailer.OUTBOX[0])
    resend = f"{B}/auth/resend-verification"

    assert client.post(resend).status_code == 401
    r = client.post(resend, headers=bearer(tokens))
    assert r.status_code == 202 and r.json()["message"] == "sent"
    assert len(mailer.OUTBOX) == 2
    assert verify(client, old).status_code == 400  # the earlier link was replaced
    assert verify(client, token_from(mailer.OUTBOX[1])).status_code == 204

    again = client.post(resend, headers=bearer(tokens))
    assert again.json()["message"] == "already_verified" and len(mailer.OUTBOX) == 2


def test_unverified_email_can_be_required_for_password_login(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "require_verified_email", True)
    register(client)
    blocked = login(client)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "email_not_verified"
    assert login(client, password=WRONG).status_code == 401  # a wrong password reveals nothing more

    assert verify(client, token_from(mailer.OUTBOX[0])).status_code == 204
    assert login(client).status_code == 200


# ---------- forgot and reset password ----------

def test_forgot_password_does_not_reveal_which_emails_exist(client):
    register(client)
    mailer.OUTBOX.clear()
    known = forgot(client, "a@example.com")
    unknown = forgot(client, "ghost@example.com")
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [m["to"] for m in mailer.OUTBOX] == ["a@example.com"]
    assert "reset" in mailer.OUTBOX[0]["subject"].lower()
    assert "http://localhost:3000/reset-password?token=" in mailer.OUTBOX[0]["body"]


def test_forgot_password_is_rate_limited_per_email(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    for _ in range(3):
        assert forgot(client, "ghost@example.com").status_code == 202
    blocked = forgot(client, "ghost@example.com")
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"


def test_password_reset_flow(client):
    register(client)
    old = login(client).json()
    mailer.OUTBOX.clear()
    forgot(client)
    token = token_from(mailer.OUTBOX[-1])

    weak = reset(client, token, "password123456")
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "weak_password"

    assert reset(client, token, NEW_PASSWORD).status_code == 204  # the weak attempt kept the link alive
    assert login(client).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 200
    assert client.get(f"{B}/users/me", headers=bearer(old)).status_code == 401
    assert client.post(f"{B}/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code == 401

    assert reset(client, token, "yet-another-pass-7").status_code == 400  # single use
    assert "password was changed" in mailer.OUTBOX[-1]["subject"].lower()
    with SessionLocal() as db:
        assert db.scalar(select(User)).email_verified_at is not None  # the link proved mailbox control


def test_a_new_reset_request_replaces_the_earlier_link(client):
    register(client)
    forgot(client)
    first = token_from(mailer.OUTBOX[-1])
    forgot(client)
    second = token_from(mailer.OUTBOX[-1])
    assert first != second
    assert reset(client, first, NEW_PASSWORD).status_code == 400
    assert reset(client, second, NEW_PASSWORD).status_code == 204


def test_expired_reset_links_are_refused(client):
    register(client)
    forgot(client)
    token = token_from(mailer.OUTBOX[-1])
    expire_all_links()
    assert reset(client, token, NEW_PASSWORD).status_code == 400


def test_reset_clears_a_lockout(client):
    register(client)
    for _ in range(5):
        assert login(client, password=WRONG).status_code == 401
    assert login(client).status_code == 429
    forgot(client)
    assert reset(client, token_from(mailer.OUTBOX[-1]), NEW_PASSWORD).status_code == 204
    assert login(client, password=NEW_PASSWORD).status_code == 200


def test_a_google_or_github_account_can_set_a_password_through_reset(client):
    with SessionLocal() as db:
        role = db.scalar(select(Role).where(Role.name == "user"))
        db.add(User(email="social@example.com", password_hash=None, roles=[role]))
        db.commit()
    assert login(client, "social@example.com").status_code == 401
    forgot(client, "social@example.com")
    assert reset(client, token_from(mailer.OUTBOX[-1]), NEW_PASSWORD).status_code == 204
    assert login(client, "social@example.com", NEW_PASSWORD).status_code == 200


def test_changing_the_password_sends_a_security_notice(client):
    register(client)
    tokens = login(client).json()
    mailer.OUTBOX.clear()
    r = client.post(
        f"{B}/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=bearer(tokens),
    )
    assert r.status_code == 200
    assert "password was changed" in mailer.OUTBOX[-1]["subject"].lower()


# ---------- the mailer itself ----------

def test_console_backend_prints_in_development_and_hides_links_elsewhere(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "email_backend", "console")
    mailer.send_email("a@example.com", "Hello", "secret-link-body")
    assert "secret-link-body" in capsys.readouterr().out

    monkeypatch.setattr(get_settings(), "env", "production")
    mailer.send_email("a@example.com", "Hello", "secret-link-body")
    assert "secret-link-body" not in capsys.readouterr().out


class FakeSMTP:
    instances = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        if FakeSMTP.fail:
            raise OSError("connection refused")
        self.calls.append(("send", message["To"], message["Subject"]))


def test_smtp_backend_and_failures_never_break_the_caller(monkeypatch):
    s = get_settings()
    for name, value in {"email_backend": "smtp", "smtp_host": "smtp.test", "smtp_port": 587,
                        "smtp_user": "u", "smtp_password": "p"}.items():
        monkeypatch.setattr(s, name, value)
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.instances, FakeSMTP.fail = [], False

    mailer.send_email("a@example.com", "Hello", "Body")
    assert FakeSMTP.instances[0].calls == ["starttls", ("login", "u", "p"), ("send", "a@example.com", "Hello")]

    FakeSMTP.fail = True
    mailer.send_email("a@example.com", "Hello", "Body")  # must not raise


def test_resend_backend_posts_to_the_api_and_survives_errors(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "email_backend", "resend")
    monkeypatch.setattr(s, "resend_api_key", "re_test")
    monkeypatch.setattr(s, "email_from", "Compass <no-reply@example.com>")
    seen, status = [], {"code": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status["code"], json={"id": "1"})

    monkeypatch.setattr(mailer, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    mailer.send_email("a@example.com", "Hello", "Body text")
    request = seen[0]
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer re_test"
    assert json.loads(request.content) == {
        "from": "Compass <no-reply@example.com>", "to": ["a@example.com"],
        "subject": "Hello", "text": "Body text",
    }

    status["code"] = 500
    mailer.send_email("a@example.com", "Hello", "Body text")  # a provider error must not raise