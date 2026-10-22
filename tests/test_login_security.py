import json
from datetime import datetime, timedelta, timezone

import pytest
import redis
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.core.passwords import check_password_policy
from app.core.security import create_access_token
from app.modules.auth.models import AuthEvent, Role, User
from tests.helpers import PASSWORD

B = "/api/v1"
WRONG = "wrong-password-1"


def register(client, email="a@example.com", password=PASSWORD):
    return client.post(f"{B}/auth/register", json={"email": email, "password": password})


def login(client, email="a@example.com", password=PASSWORD):
    return client.post(f"{B}/auth/login", json={"email": email, "password": password})


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def events():
    with SessionLocal() as db:
        return list(db.scalars(select(AuthEvent).order_by(AuthEvent.created_at)))


def get_user(email):
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.email == email))


# ---------- password policy ----------

@pytest.mark.parametrize(
    "pw", ["password123456", "1234567890", "qwertyuiop12", "aaaaaaaaaaaa", "Welcome12345!"]
)
def test_predictable_passwords_are_rejected(pw):
    with pytest.raises(AppError) as exc:
        check_password_policy(pw)
    assert exc.value.code == "weak_password"


def test_password_containing_email_name_is_rejected():
    with pytest.raises(AppError):
        check_password_policy("alicesmith-99", "alicesmith@example.com")


def test_reasonable_passwords_are_accepted():
    check_password_policy("correct-horse-1", "a@example.com")
    check_password_policy("blue-Tractor-Dancing-42")


def test_register_rejects_weak_password(client):
    r = register(client, password="password123456")
    assert r.status_code == 422 and r.json()["error"]["code"] == "weak_password"


# ---------- lockout ----------

def test_account_locks_after_repeated_failures_and_recovers(client):
    register(client)
    for _ in range(5):
        assert login(client, password=WRONG).status_code == 401
    locked = login(client)  # even the correct password is refused while locked
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_locked"
    assert int(locked.headers["retry-after"]) > 0
    assert "account_locked" in [e.event for e in events()]

    with SessionLocal() as db:  # simulate the lock expiring
        user = db.scalar(select(User).where(User.email == "a@example.com"))
        user.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert login(client).status_code == 200
    assert get_user("a@example.com").failed_login_count == 0


def test_a_successful_login_resets_the_failure_count(client):
    register(client)
    for _ in range(3):
        assert login(client, password=WRONG).status_code == 401
    assert login(client).status_code == 200
    for _ in range(4):
        assert login(client, password=WRONG).status_code == 401
    assert login(client).status_code == 200  # 4 failures since the reset: not locked


# ---------- audit trail ----------

def test_security_events_are_recorded_without_leaking_emails(client):
    register(client)
    login(client, email="ghost@example.com", password=WRONG)
    login(client, password=WRONG)
    login(client)
    assert [e.event for e in events()] == ["register", "login_failed", "login_failed", "login_success"]

    unknown = next(e for e in events() if e.user_id is None)
    assert unknown.detail["reason"] == "unknown_account"
    assert "ghost" not in json.dumps(unknown.detail)


# ---------- rate limiting ----------

def test_registration_is_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    for n in range(10):
        assert register(client, f"user{n}@example.com").status_code == 201
    blocked = register(client, "user10@example.com")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) > 0


def test_login_is_rate_limited_per_email(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    for _ in range(10):
        assert login(client, email="ghost@example.com", password=WRONG).status_code == 401
    blocked = login(client, email="ghost@example.com", password=WRONG)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"


def test_rate_limiter_fails_open_when_redis_is_down(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)

    def down():
        raise redis.ConnectionError("redis is down")

    monkeypatch.setattr("app.core.ratelimit.get_redis", down)
    assert register(client).status_code == 201


# ---------- sessions ----------

def test_logout_all_ends_every_session(client):
    register(client)
    first, second = login(client).json(), login(client).json()
    assert client.post(f"{B}/auth/logout-all").status_code == 401
    assert client.post(f"{B}/auth/logout-all", headers=bearer(first)).status_code == 204

    for tokens in (first, second):
        assert client.get(f"{B}/users/me", headers=bearer(tokens)).status_code == 401
        assert client.post(f"{B}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert login(client).status_code == 200
    assert "logout_all" in [e.event for e in events()]


def test_change_password_ends_other_sessions_but_keeps_this_one(client):
    register(client)
    old = login(client).json()
    url = f"{B}/auth/change-password"
    new_password = "another-long-pass-9"

    def change(current, new):
        return client.post(
            url, json={"current_password": current, "new_password": new}, headers=bearer(old)
        )

    assert change(WRONG, new_password).status_code == 400
    assert change(PASSWORD, "password123456").status_code == 422
    assert change(PASSWORD, PASSWORD).status_code == 422

    ok = change(PASSWORD, new_password)
    assert ok.status_code == 200
    fresh = ok.json()
    assert client.get(f"{B}/users/me", headers=bearer(old)).status_code == 401
    assert client.post(f"{B}/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code == 401
    assert client.get(f"{B}/users/me", headers=bearer(fresh)).status_code == 200
    assert login(client).status_code == 401  # the old password no longer works
    assert login(client, password=new_password).status_code == 200


# ---------- accounts without a password (Google/GitHub only) ----------

def test_account_without_a_password_cannot_use_password_login(client):
    with SessionLocal() as db:
        role = db.scalar(select(Role).where(Role.name == "user"))
        user = User(email="oauth@example.com", password_hash=None, roles=[role])
        db.add(user)
        db.commit()
        user_id = user.id

    r = login(client, email="oauth@example.com", password="anything-long-1")
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"

    token = create_access_token(user_id, ["user"], 0)
    change = client.post(
        f"{B}/auth/change-password",
        json={"current_password": "x", "new_password": "another-long-pass-9"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert change.status_code == 422 and change.json()["error"]["code"] == "no_password_set"