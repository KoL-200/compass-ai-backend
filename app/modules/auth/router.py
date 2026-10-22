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
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=201,
    dependencies=[Depends(ip_limit("register", 10, 3600))],
)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    return service.register(db, body.email, body.password, ip=client_ip(request))


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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    enforce("change-password", str(user.id), 5, 900)
    return service.change_password(
        db, user, body.current_password, body.new_password, ip=client_ip(request)
    )


@users_router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user