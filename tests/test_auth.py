from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.modules.auth.dependencies import require_role

BASE = "/api/v1"
PASSWORD = "correct-horse-1"


def register(client, email="a@example.com", password=PASSWORD):
    return client.post(f"{BASE}/auth/register", json={"email": email, "password": password})


def login(client, email="a@example.com", password=PASSWORD):
    return client.post(f"{BASE}/auth/login", json={"email": email, "password": password})


def test_register_success_hides_password(client):
    r = register(client, email="Alice@Example.com")
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert "password" not in body and "password_hash" not in body


def test_register_duplicate_email_is_case_insensitive(client):
    register(client, email="a@example.com")
    r = register(client, email="A@EXAMPLE.COM")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_taken"


def test_register_short_password_rejected(client):
    r = register(client, password="short")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_login_success_and_me(client):
    register(client)
    r = login(client)
    assert r.status_code == 200
    tokens = r.json()
    me = client.get(
        f"{BASE}/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"


def test_login_wrong_password_and_unknown_email_look_identical(client):
    register(client)
    wrong = login(client, password="not-the-password")
    unknown = login(client, email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_me_requires_valid_token(client):
    assert client.get(f"{BASE}/users/me").status_code == 401
    bad = client.get(f"{BASE}/users/me", headers={"Authorization": "Bearer garbage"})
    assert bad.status_code == 401


def test_refresh_rotates_token(client):
    register(client)
    first = login(client).json()
    r = client.post(f"{BASE}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["refresh_token"] != first["refresh_token"]


def test_refresh_token_reuse_revokes_whole_family(client):
    register(client)
    first = login(client).json()
    second = client.post(
        f"{BASE}/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()

    # Replaying the OLD token is treated as theft...
    replay = client.post(f"{BASE}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "token_reused"

    # ...so even the newest token in that chain must now be dead.
    newest = client.post(f"{BASE}/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert newest.status_code == 401


def test_logout_revokes_refresh_token_and_is_idempotent(client):
    register(client)
    tokens = login(client).json()
    out = client.post(f"{BASE}/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert out.status_code == 204
    again = client.post(f"{BASE}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == 401
    assert (
        client.post(f"{BASE}/auth/logout", json={"refresh_token": "unknown"}).status_code == 204
    )


def test_require_role_blocks_user_without_role():
    checker = require_role("admin")
    plain_user = SimpleNamespace(roles=[SimpleNamespace(name="user")])
    with pytest.raises(AppError) as exc:
        checker(user=plain_user)
    assert exc.value.status_code == 403

    admin = SimpleNamespace(roles=[SimpleNamespace(name="admin")])
    assert checker(user=admin) is admin