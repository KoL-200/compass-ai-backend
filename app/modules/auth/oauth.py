"""Google and GitHub sign-in: the OAuth 2.0 authorization-code flow with PKCE."""
import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode

import httpx
import jwt

from app.core.config import get_settings
from app.core.errors import AppError

Provider = Literal["google", "github"]
COOKIE = "oauth_flow"
COOKIE_PATH = "/api/v1/auth/oauth"
FLOW_TTL_SECONDS = 600

PROVIDERS = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "openid email profile",
    },
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "scope": "read:user user:email",
    },
}
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
GITHUB_USER = "https://api.github.com/user"
GITHUB_EMAILS = "https://api.github.com/user/emails"


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str  # the provider's permanent id for this person
    email: str  # always a provider-verified address, lower-cased
    name: str | None


def _credentials(provider: str) -> tuple[str, str]:
    s = get_settings()
    if provider == "google":
        client_id, secret = s.google_client_id, s.google_client_secret
    else:
        client_id, secret = s.github_client_id, s.github_client_secret
    if not client_id or not secret:
        raise AppError(
            503, "provider_not_configured", f"Sign-in with {provider.title()} is not configured"
        )
    return client_id, secret


def redirect_uri(provider: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/api/v1/auth/oauth/{provider}/callback"


def cookie_secure() -> bool:
    return get_settings().env != "development"


# ---------- PKCE and the signed flow cookie ----------

def new_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)  # 86 characters, within the 43-128 allowed
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def make_flow_token(provider: str, state: str, verifier: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "typ": "oauth_flow",
        "p": provider,
        "s": state,
        "v": verifier,
        "exp": now + timedelta(seconds=FLOW_TTL_SECONDS),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


def read_flow_token(token: str | None, provider: str, state: str | None) -> str:
    """Return the PKCE verifier, or raise if the cookie is missing, forged, expired,
    for a different provider, or does not match the state the provider sent back."""
    invalid = AppError(
        400, "oauth_state_invalid", "The sign-in attempt could not be verified. Please try again."
    )
    if not token or not state:
        raise invalid
    try:
        data = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise invalid
    same_state = hmac.compare_digest(str(data.get("s", "")).encode(), state.encode())
    if data.get("typ") != "oauth_flow" or data.get("p") != provider or not same_state:
        raise invalid
    return data["v"]


def build_authorize_url(provider: str, state: str, challenge: str) -> str:
    client_id, _ = _credentials(provider)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(provider),
        "response_type": "code",
        "scope": PROVIDERS[provider]["scope"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if provider == "google":
        params["prompt"] = "select_account"
    return f"{PROVIDERS[provider]['authorize']}?{urlencode(params)}"


# ---------- talking to the provider ----------

def _http() -> httpx.Client:
    return httpx.Client(timeout=10.0)  # tests replace this with a fake


def _json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return None


def _failed() -> AppError:
    return AppError(400, "oauth_failed", "Sign-in with the provider failed. Please try again.")


def _google_identity(http: httpx.Client, access_token: str) -> Identity:
    r = http.get(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {access_token}"})
    data = _json(r) if r.status_code == 200 else None
    if not isinstance(data, dict) or not data.get("sub") or not data.get("email"):
        raise _failed()
    if str(data.get("email_verified")).lower() != "true":
        raise AppError(400, "email_not_verified", "Your Google email address is not verified")
    return Identity("google", str(data["sub"]), data["email"].strip().lower(), data.get("name"))


def _github_identity(http: httpx.Client, access_token: str) -> Identity:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "compass-ai",
    }
    user_r = http.get(GITHUB_USER, headers=headers)
    emails_r = http.get(GITHUB_EMAILS, headers=headers)
    profile = _json(user_r) if user_r.status_code == 200 else None
    rows = _json(emails_r) if emails_r.status_code == 200 else None
    if not isinstance(profile, dict) or "id" not in profile or not isinstance(rows, list):
        raise _failed()
    verified = [e for e in rows if isinstance(e, dict) and e.get("verified") and e.get("email")]
    if not verified:
        raise AppError(400, "email_not_verified", "Your GitHub account has no verified email address")
    chosen = next((e for e in verified if e.get("primary")), verified[0])
    name = profile.get("name") or profile.get("login")
    return Identity("github", str(profile["id"]), chosen["email"].strip().lower(), name)


def fetch_identity(provider: str, code: str, verifier: str) -> Identity:
    client_id, secret = _credentials(provider)
    try:
        with _http() as http:
            token_r = http.post(
                PROVIDERS[provider]["token"],
                data={
                    "client_id": client_id,
                    "client_secret": secret,
                    "code": code,
                    "redirect_uri": redirect_uri(provider),
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
            body = _json(token_r) if token_r.status_code == 200 else None
            access_token = body.get("access_token") if isinstance(body, dict) else None
            if not access_token:
                raise _failed()  # note: the provider's error text is deliberately not passed on
            if provider == "google":
                return _google_identity(http, access_token)
            return _github_identity(http, access_token)
    except httpx.HTTPError:
        raise AppError(502, "oauth_unavailable", "The provider could not be reached. Try again later.")