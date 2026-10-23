import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.modules.auth import oauth
from app.modules.auth.models import AuthEvent, OAuthAccount, OAuthLoginCode, User
from tests.helpers import PASSWORD

B = "/api/v1"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    s = get_settings()
    values = {
        "google_client_id": "gid", "google_client_secret": "gsecret",
        "github_client_id": "hid", "github_client_secret": "hsecret",
        "public_base_url": "http://localhost:8000",
        "frontend_callback_url": "http://localhost:8000/oauth-placeholder",
    }
    for name, value in values.items():
        monkeypatch.setattr(s, name, value)


# ---------- helpers ----------

def register(client, email):
    return client.post(f"{B}/auth/register", json={"email": email, "password": PASSWORD})


def login(client, email):
    return client.post(f"{B}/auth/login", json={"email": email, "password": PASSWORD})


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def google_user(email, verified=True, sub="g-123"):
    return {"sub": sub, "email": email, "email_verified": verified, "name": "Test User"}


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def fake_provider(monkeypatch, *, google=None, github=None, emails=None, token_json=None):
    """Replace the HTTP client so no request leaves the test."""
    seen = {"token_requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in ("https://oauth2.googleapis.com/token", "https://github.com/login/oauth/access_token"):
            seen["token_requests"].append(parse_qs(request.content.decode()))
            return httpx.Response(200, json=token_json or {"access_token": "provider-token"})
        assert request.headers["authorization"] == "Bearer provider-token"
        if url == "https://openidconnect.googleapis.com/v1/userinfo":
            return httpx.Response(200, json=google)
        if url == "https://api.github.com/user":
            return httpx.Response(200, json=github)
        if url == "https://api.github.com/user/emails":
            return httpx.Response(200, json=emails)
        return httpx.Response(404)

    monkeypatch.setattr(oauth, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def start(client, provider="google"):
    r = client.get(f"{B}/auth/oauth/{provider}/login", follow_redirects=False)
    assert r.status_code == 302, r.text
    return r, parse_qs(urlparse(r.headers["location"]).query)


def finish(client, provider, state, **params):
    params = {"code": "provider-code", "state": state, **params}
    r = client.get(f"{B}/auth/oauth/{provider}/callback", params=params, follow_redirects=False)
    assert r.status_code == 302
    return parse_qs(urlparse(r.headers["location"]).query)


def sign_in(client, provider="google"):
    _, query = start(client, provider)
    return finish(client, provider, query["state"][0])


def exchange(client, code):
    return client.post(f"{B}/auth/oauth/exchange", json={"code": code})


def users():
    with SessionLocal() as db:
        return list(db.scalars(select(User)))


def accounts():
    with SessionLocal() as db:
        return list(db.scalars(select(OAuthAccount)))


def event_names():
    with SessionLocal() as db:
        return [e.event for e in db.scalars(select(AuthEvent).order_by(AuthEvent.created_at))]


# ---------- starting the flow ----------

def test_login_redirects_to_google_with_state_and_pkce(client):
    r, query = start(client)
    location = urlparse(r.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert query["client_id"] == ["gid"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/auth/oauth/google/callback"]
    assert query["code_challenge_method"] == ["S256"] and "email" in query["scope"][0]
    assert len(query["state"][0]) >= 32
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_unknown_or_unconfigured_providers_are_refused(client, monkeypatch):
    assert client.get(f"{B}/auth/oauth/facebook/login", follow_redirects=False).status_code == 422
    monkeypatch.setattr(get_settings(), "google_client_id", "")
    r = client.get(f"{B}/auth/oauth/google/login", follow_redirects=False)
    assert r.status_code == 503 and r.json()["error"]["code"] == "provider_not_configured"


# ---------- Google ----------

def test_google_sign_up_creates_a_verified_account_without_a_password(client, monkeypatch):
    seen = fake_provider(monkeypatch, google=google_user("new@example.com"))
    _, authorize = start(client)
    result = finish(client, "google", authorize["state"][0])
    assert "code" in result and "error" not in result

    sent = seen["token_requests"][0]
    assert sent["code"] == ["provider-code"] and sent["client_secret"] == ["gsecret"]
    assert pkce_challenge(sent["code_verifier"][0]) == authorize["code_challenge"][0]

    tokens = exchange(client, result["code"][0])
    assert tokens.status_code == 200
    assert client.get(f"{B}/users/me", headers=bearer(tokens.json())).json()["email"] == "new@example.com"

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "new@example.com"))
        assert user.password_hash is None and user.email_verified_at is not None
        assert [role.name for role in user.roles] == ["user"]
        link = db.scalar(select(OAuthAccount))
        assert (link.provider, link.provider_user_id, link.user_id) == ("google", "g-123", user.id)
    assert {"register", "login_success"} <= set(event_names())


def test_returning_google_user_signs_into_the_same_account(client, monkeypatch):
    fake_provider(monkeypatch, google=google_user("new@example.com"))
    assert exchange(client, sign_in(client)["code"][0]).status_code == 200
    assert exchange(client, sign_in(client)["code"][0]).status_code == 200
    assert len(users()) == 1 and len(accounts()) == 1


def test_provider_email_must_be_verified(client, monkeypatch):
    fake_provider(monkeypatch, google=google_user("new@example.com", verified=False))
    assert sign_in(client)["error"] == ["email_not_verified"]
    assert users() == []


# ---------- linking to existing accounts ----------

def test_linking_keeps_the_password_of_an_account_with_a_verified_email(client, monkeypatch):
    register(client, "owner@example.com")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        user.email_verified_at = datetime.now(timezone.utc)
        db.commit()
    fake_provider(monkeypatch, google=google_user("owner@example.com"))
    assert "code" in sign_in(client)
    assert login(client, "owner@example.com").status_code == 200  # password still works
    assert len(users()) == 1 and len(accounts()) == 1


def test_linking_to_an_unverified_password_account_removes_that_password(client, monkeypatch):
    register(client, "owner@example.com")  # stands in for someone pre-registering the email
    old = login(client, "owner@example.com").json()
    fake_provider(monkeypatch, google=google_user("owner@example.com"))
    assert "code" in sign_in(client)

    assert login(client, "owner@example.com").status_code == 401  # the planted password is gone
    assert client.get(f"{B}/users/me", headers=bearer(old)).status_code == 401
    assert client.post(f"{B}/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code == 401
    assert "password_removed_on_link" in event_names()


def test_disabled_accounts_cannot_sign_in(client, monkeypatch):
    fake_provider(monkeypatch, google=google_user("new@example.com"))
    sign_in(client)
    with SessionLocal() as db:
        db.execute(update(User).values(is_active=False))
        db.commit()
    assert sign_in(client)["error"] == ["account_disabled"]


# ---------- GitHub ----------

def test_github_sign_in_uses_the_primary_verified_email(client, monkeypatch):
    fake_provider(
        monkeypatch,
        github={"id": 42, "login": "octo", "name": "Octo"},
        emails=[
            {"email": "Other@Example.com", "primary": False, "verified": True},
            {"email": "main@example.com", "primary": True, "verified": True},
        ],
    )
    result = sign_in(client, "github")
    assert "code" in result
    assert [u.email for u in users()] == ["main@example.com"]
    assert [(a.provider, a.provider_user_id) for a in accounts()] == [("github", "42")]


def test_github_without_a_verified_email_is_refused(client, monkeypatch):
    fake_provider(
        monkeypatch,
        github={"id": 42, "login": "octo"},
        emails=[{"email": "main@example.com", "primary": True, "verified": False}],
    )
    assert sign_in(client, "github")["error"] == ["email_not_verified"]
    assert users() == []


# ---------- attacks and failures ----------

def test_wrong_state_or_missing_cookie_is_rejected(client, monkeypatch):
    fake_provider(monkeypatch, google=google_user("new@example.com"))
    _, query = start(client)
    assert finish(client, "google", "not-the-state")["error"] == ["oauth_state_invalid"]
    assert finish(client, "google", query["state"][0])["error"] == ["oauth_state_invalid"]  # cookie was cleared
    assert users() == []


def test_a_flow_started_for_one_provider_cannot_finish_for_another(client, monkeypatch):
    fake_provider(monkeypatch, github={"id": 1, "login": "x"},
                  emails=[{"email": "a@example.com", "primary": True, "verified": True}])
    _, query = start(client, "google")
    assert finish(client, "github", query["state"][0])["error"] == ["oauth_state_invalid"]
    assert users() == []


def test_cancelling_at_the_provider_is_handled(client):
    start(client)
    r = client.get(f"{B}/auth/oauth/google/callback", params={"error": "access_denied", "state": "x"},
                   follow_redirects=False)
    assert parse_qs(urlparse(r.headers["location"]).query)["error"] == ["oauth_denied"]


def test_a_failed_token_exchange_is_reported_without_provider_details(client, monkeypatch):
    fake_provider(monkeypatch, token_json={"error": "bad_verification_code"})
    assert sign_in(client)["error"] == ["oauth_failed"]
    assert users() == []
    assert "oauth_failed" in event_names()


def test_login_codes_are_single_use_and_expire(client, monkeypatch):
    fake_provider(monkeypatch, google=google_user("new@example.com"))
    code = sign_in(client)["code"][0]
    assert exchange(client, code).status_code == 200
    again = exchange(client, code)
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_code"

    fresh = sign_in(client)["code"][0]
    with SessionLocal() as db:
        db.execute(update(OAuthLoginCode).where(OAuthLoginCode.used_at.is_(None)).values(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=5)))
        db.commit()
    assert exchange(client, fresh).status_code == 400
    assert exchange(client, "x" * 20).status_code == 400


# ---------- the development placeholder ----------

def test_placeholder_page_shows_the_code_safely_and_is_dev_only(client, monkeypatch):
    page = client.get("/oauth-placeholder", params={"code": "abc123xyz789"})
    assert page.status_code == 200 and "abc123xyz789" in page.text
    assert page.headers["referrer-policy"] == "no-referrer"

    failed = client.get("/oauth-placeholder", params={"error": "<script>alert(1)</script>"})
    assert "<script>" not in failed.text and "&lt;script&gt;" in failed.text

    monkeypatch.setattr(get_settings(), "env", "production")
    assert client.get("/oauth-placeholder", params={"code": "abc123xyz789"}).status_code == 404