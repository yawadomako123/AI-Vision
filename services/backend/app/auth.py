"""Operator authentication: argon2 password hashes, server-side sessions in an HttpOnly cookie.

CSRF: the session cookie is SameSite=Lax, and every state-changing request must carry the
custom header `X-Requested-With: aivision`. Browsers can't add custom headers cross-origin
without a CORS preflight, which only the configured dashboard origins pass.
"""

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, WebSocket, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User, UserSession

COOKIE = "aiv_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "aivision"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MIN_PASSWORD = 10

_hasher = PasswordHasher()
# Precomputed so unknown usernames take as long as wrong passwords (no user enumeration by timing).
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(user: User | None, password: str) -> bool:
    try:
        return _hasher.verify(user.password_hash if user else _DUMMY_HASH, password) and user is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # sqlite drops tzinfo


def start_session(db: Session, user: User, response: Response, request: Request | None = None) -> None:
    token = secrets.token_urlsafe(32)
    # Secure when configured, or automatically when the browser reached us over HTTPS (e.g.
    # through `tailscale serve`, which forwards X-Forwarded-Proto; uvicorn runs with --proxy-headers).
    secure = settings.cookie_secure or (request is not None and request.url.scheme == "https")
    db.add(UserSession(token_hash=_token_hash(token), user_id=user.id,
                       expires_at=_now() + timedelta(hours=settings.session_hours)))
    db.execute(delete(UserSession).where(UserSession.expires_at < _now()))  # housekeeping
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=secure,
                        max_age=settings.session_hours * 3600, path="/")


def end_session(db: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(token)))
    response.delete_cookie(COOKIE, path="/")


def end_all_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))


def user_from_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if sess is None or _aware(sess.expires_at) < _now() or not sess.user.active:
        return None
    # Sliding expiry, written at most every few minutes.
    new_expiry = _now() + timedelta(hours=settings.session_hours)
    if new_expiry - _aware(sess.expires_at) > timedelta(minutes=5):
        sess.expires_at = new_expiry
        db.commit()
    return sess.user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_from_token(db, request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return user


def websocket_user(ws: WebSocket, db: Session) -> User | None:
    return user_from_token(db, ws.cookies.get(COOKIE))


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


class LoginThrottle:
    """Locks a username+IP pair for `lock_seconds` after `max_failures` failed logins."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 900, lock_seconds: int = 900):
        self.max_failures, self.window, self.lock = max_failures, window_seconds, lock_seconds
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def check(self, username: str, ip: str | None) -> None:
        key = (username.lower(), ip or "")
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if time.monotonic() - t < self.window]
            self._failures[key] = recent
            if len(recent) >= self.max_failures and time.monotonic() - recent[-1] < self.lock:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                    "too many failed attempts; try again in 15 minutes")

    def failed(self, username: str, ip: str | None) -> None:
        with self._lock:
            self._failures.setdefault((username.lower(), ip or ""), []).append(time.monotonic())

    def succeeded(self, username: str, ip: str | None) -> None:
        with self._lock:
            self._failures.pop((username.lower(), ip or ""), None)


throttle = LoginThrottle()
