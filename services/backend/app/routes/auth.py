from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import (
    COOKIE, CSRF_HEADER, CSRF_VALUE, client_ip, current_user, end_all_sessions, end_session,
    hash_password, start_session, throttle, user_from_token, verify_password,
)
from app.db import get_db
from app.models import User
from app.schemas import AuthStatus, LoginIn, PasswordChange, SetupIn, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _csrf(request: Request) -> None:
    # Login/setup have no session yet, but still must not be triggerable cross-site.
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request, db: Session = Depends(get_db)):
    """Public: tells the dashboard whether to show first-run setup, login, or the app."""
    setup_required = (db.scalar(select(func.count()).select_from(User)) or 0) == 0
    user = user_from_token(db, request.cookies.get(COOKIE))
    return AuthStatus(setup_required=setup_required, user=UserOut.model_validate(user) if user else None)


@router.post("/setup", response_model=UserOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(_csrf)])
def setup(body: SetupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """First run only: create the first admin. Refused once any user exists."""
    if db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(status.HTTP_409_CONFLICT, "setup already completed")
    user = User(username=body.username, password_hash=hash_password(body.password), role="admin",
                last_login_at=datetime.now(timezone.utc))
    db.add(user)
    db.flush()
    start_session(db, user, response)
    audit(db, request, user, "setup", f"user:{user.username}")
    db.commit()
    return user


@router.post("/login", response_model=UserOut, dependencies=[Depends(_csrf)])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    throttle.check(body.username, ip)
    user = db.scalar(select(User).where(func.lower(User.username) == body.username.lower()))
    if not verify_password(user, body.password) or not user.active:
        throttle.failed(body.username, ip)
        audit(db, request, body.username[:60], "login_failed")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
    throttle.succeeded(body.username, ip)
    user.last_login_at = datetime.now(timezone.utc)
    start_session(db, user, response)
    audit(db, request, user, "login")
    db.commit()
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    end_session(db, request, response)
    audit(db, request, user, "logout")
    db.commit()


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChange, request: Request, response: Response,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(user, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current password is wrong")
    user.password_hash = hash_password(body.new_password)
    end_all_sessions(db, user.id)  # sign out everywhere else...
    start_session(db, user, response)  # ...but keep this browser signed in
    audit(db, request, user, "password_change", f"user:{user.username}")
    db.commit()
