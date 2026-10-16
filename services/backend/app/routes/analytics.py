from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Camera, Event

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary")
def summary(hours: int = Query(default=24, ge=1, le=24 * 31), db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    cameras = dict(db.execute(select(Camera.status, func.count()).group_by(Camera.status)).all())
    by_type = dict(db.execute(
        select(Event.event_type, func.count()).where(Event.occurred_at >= since).group_by(Event.event_type)
    ).all())
    unreviewed = db.scalar(select(func.count()).select_from(Event).where(Event.status == "unreviewed"))
    return {
        "window_hours": hours,
        "cameras": {"total": sum(cameras.values()), "by_status": cameras},
        "events": {"total": sum(by_type.values()), "by_type": by_type},
        "unreviewed": unreviewed or 0,
    }
