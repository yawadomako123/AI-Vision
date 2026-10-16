from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import end_all_sessions, hash_password, require_admin
from app.db import get_db
from app.models import AuditLog, User
from app.schemas import AuditPage, UserCreate, UserOut, UserUpdate

router = APIRouter(tags=["users"])


@router.get("/api/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.id)).all()


@router.post("/api/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, request: Request, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    if db.scalar(select(User).where(func.lower(User.username) == body.username.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "username already taken")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    audit(db, request, admin, "user.create", f"user:{body.username}", role=body.role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "username already taken")
    return user


@router.patch("/api/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    changes = body.model_dump(exclude_unset=True)

    losing_admin = user.role == "admin" and user.active and (
        changes.get("role", "admin") != "admin" or changes.get("active", True) is False
    )
    if losing_admin:
        admins = db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active))
        if admins <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "there must be at least one active admin")

    if "password" in changes:
        user.password_hash = hash_password(changes.pop("password"))
        end_all_sessions(db, user.id)
        changes["password"] = "changed"
    for field in ("role", "active"):
        if field in changes:
            setattr(user, field, changes[field])
    if changes.get("active") is False:
        end_all_sessions(db, user.id)
    audit(db, request, admin, "user.update", f"user:{user.username}", **changes)
    db.commit()
    return user


@router.get("/api/audit", response_model=AuditPage)
def audit_log(limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0),
              action: str | None = None, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    filters = [AuditLog.action == action] if action else []
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    rows = db.scalars(select(AuditLog).where(*filters).order_by(AuditLog.id.desc()).limit(limit).offset(offset))
    return AuditPage(total=total, items=list(rows))
