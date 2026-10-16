from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def audit(db: Session, request: Request | None, user: User | str | None, action: str,
          target: str | None = None, **detail) -> None:
    """Record an action. Added to the caller's transaction; committed with it."""
    username = user.username if isinstance(user, User) else user
    ip = request.client.host if request is not None and request.client else None
    db.add(AuditLog(username=username, action=action, target=target, detail=detail, ip=ip))
