from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.ratelimit import client_ip, enforce, hash_ident, ip_limit
from app.modules.auth import service
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    OAuthExchangeRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    StatusOut,
    TokenRequest
)

import html
import secrets
from urllib.parse import urlencode

from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.auth import audit, oauth, oauth_service

import logging

from fastapi import BackgroundTasks

from app.core.mailer import send_email
from app.modules.auth import email_flows

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=201,
    dependencies=[Depends(ip_limit("register", 10, 3600))],
)
def register(
    body: RegisterRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = service.register(db, body.email, body.password, ip=client_ip(request))
    try:
        background.add_task(send_email, *email_flows.verification_message(db, user))
    except Exception:  # the account exists; the user can ask for a new link
        db.rollback()
        log.exception("Could not prepare the verification email")
    return user


@router.post(
    "/login",
    response_model=TokenPair,
    dependencies=[Depends(ip_limit("login-ip", 20, 900))],
)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    enforce("login-email", hash_ident(body.email), 10, 900)
    return service.login(db, body.email, body.password, ip=client_ip(request))


@router.post(
    "/refresh",
    response_model=TokenPair,
    dependencies=[Depends(ip_limit("refresh", 60, 60))],
)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    return service.refresh(db, body.refresh_token, ip=client_ip(request))


@router.post("/logout", status_code=204)
def logout(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    service.logout(db, body.refresh_token, ip=client_ip(request))
    return Response(status_code=204)


@router.post("/logout-all", status_code=204)
def logout_all(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    service.logout_all(db, user, ip=client_ip(request))
    return Response(status_code=204)


@router.post("/change-password", response_model=TokenPair)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce("change-password", str(user.id), 5, 900)
    pair = service.change_password(
        db, user, body.current_password, body.new_password, ip=client_ip(request)
    )
    background.add_task(send_email, *email_flows.password_changed_notice(user))
    return pair



@users_router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user

# ---------- Google and GitHub sign-in ----------

def _frontend_redirect(**params) -> RedirectResponse:
    url = get_settings().frontend_callback_url
    separator = "&" if "?" in url else "?"
    response = RedirectResponse(f"{url}{separator}{urlencode(params)}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(oauth.COOKIE, path=oauth.COOKIE_PATH)
    return response


@router.get("/oauth/{provider}/login", dependencies=[Depends(ip_limit("oauth-start", 30, 300))])
def oauth_login(provider: oauth.Provider):
    state = secrets.token_urlsafe(32)
    verifier, challenge = oauth.new_pkce()
    url = oauth.build_authorize_url(provider, state, challenge)  # 503 if not configured
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        oauth.COOKIE,
        oauth.make_flow_token(provider, state, verifier),
        max_age=oauth.FLOW_TTL_SECONDS,
        httponly=True,
        secure=oauth.cookie_secure(),
        samesite="lax",  # must be Lax: the provider sends the browser back with a top-level GET
        path=oauth.COOKIE_PATH,
    )
    return response


@router.get("/oauth/{provider}/callback", dependencies=[Depends(ip_limit("oauth-callback", 30, 300))])
def oauth_callback(
    provider: oauth.Provider,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    try:
        if error:  # for example the user pressed "Cancel" at the provider
            raise AppError(400, "oauth_denied", "Sign-in was cancelled")
        verifier = oauth.read_flow_token(request.cookies.get(oauth.COOKIE), provider, state)
        if not code:
            raise AppError(400, "oauth_failed", "Sign-in with the provider failed")
        identity = oauth.fetch_identity(provider, code, verifier)
        user = oauth_service.resolve_user(db, identity, ip)
        return _frontend_redirect(code=oauth_service.create_login_code(db, user))
    except AppError as exc:
        db.rollback()
        audit.record(db, "oauth_failed", ip=ip, provider=provider, reason=exc.code)
        db.commit()
        return _frontend_redirect(error=exc.code)


@router.post(
    "/oauth/exchange",
    response_model=TokenPair,
    dependencies=[Depends(ip_limit("oauth-exchange", 30, 300))],
)
def oauth_exchange(body: OAuthExchangeRequest, request: Request, db: Session = Depends(get_db)):
    return oauth_service.exchange_login_code(db, body.code, ip=client_ip(request))


# ---------- placeholder for the missing frontend (development only) ----------

placeholder_router = APIRouter(include_in_schema=False)


@placeholder_router.get("/oauth-placeholder", response_class=HTMLResponse)
def oauth_placeholder(code: str | None = None, error: str | None = None):
    if get_settings().env != "development":
        raise AppError(404, "not_found", "Not found")
    if code:
        body = (
            "<p>Signed in. Copy this one-time code (valid for 60 seconds) and send it to "
            "<code>POST /api/v1/auth/oauth/exchange</code> in Swagger:</p>"
            f"<pre>{html.escape(code)}</pre>"
        )
    else:
        body = f"<p>Sign-in failed: <code>{html.escape(error or 'unknown')}</code></p>"
    return HTMLResponse(
        f"<!doctype html><title>Sign-in placeholder</title>{body}",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )

# ---------- email verification and password reset ----------

@router.post(
    "/verify-email",
    status_code=204,
    dependencies=[Depends(ip_limit("verify-email", 20, 3600))],
)
def verify_email(body: TokenRequest, request: Request, db: Session = Depends(get_db)):
    email_flows.verify_email(db, body.token, ip=client_ip(request))
    return Response(status_code=204)


@router.post("/resend-verification", response_model=StatusOut, status_code=202)
def resend_verification(
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce("resend-verification", str(user.id), 3, 3600)
    if user.email_verified_at is not None:
        return {"message": "already_verified"}
    background.add_task(send_email, *email_flows.verification_message(db, user))
    return {"message": "sent"}


@router.post(
    "/forgot-password",
    response_model=StatusOut,
    status_code=202,
    dependencies=[Depends(ip_limit("forgot-ip", 10, 3600))],
)
def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    enforce("forgot-email", hash_ident(body.email), 3, 3600)
    outgoing = email_flows.request_password_reset(db, body.email, ip=client_ip(request))
    if outgoing is not None:
        background.add_task(send_email, *outgoing)
    # The same answer whether or not the address has an account.
    return {"message": "If that address has an account, a reset link is on its way."}


@router.post(
    "/reset-password",
    status_code=204,
    dependencies=[Depends(ip_limit("reset-password", 10, 3600))],
)
def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    notice = email_flows.reset_password(db, body.token, body.new_password, ip=client_ip(request))
    background.add_task(send_email, *notice)
    return Response(status_code=204)