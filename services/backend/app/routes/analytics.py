import shutil
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import current_user, require_admin
from app.config import settings as app_settings
from app.db import get_db
from app.live import worker_contact
from app.models import Camera, CountSample, Event, User
from app.schemas import SettingsOut, SettingsUpdate
from app.settings_store import get_settings, update_settings

router = APIRouter(tags=["analytics"])


@router.get("/api/analytics/summary")
def summary(hours: int = Query(default=24, ge=1, le=24 * 31), _: User = Depends(current_user),
            db: Session = Depends(get_db)):
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


def _floor(dt: datetime, bucket: str) -> datetime:
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return dt.replace(hour=0) if bucket == "day" else dt


@router.get("/api/analytics/timeseries")
def timeseries(
    hours: int = Query(default=24, ge=1, le=24 * 31),
    bucket: Literal["hour", "day"] = "hour",
    camera_id: int | None = None,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Object counts (peak and average seen at once) and event counts per time bucket.

    Aggregated in Python to stay database-agnostic; at MVP scale (a few cameras, per-minute
    samples) a month of data is a few hundred thousand rows at most.
    """
    now = datetime.now(timezone.utc)
    start = _floor(now - timedelta(hours=hours), bucket) + (timedelta(hours=1) if bucket == "hour" else timedelta(days=1))
    step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    buckets = []
    t = start
    while t <= now:
        buckets.append(t)
        t += step
    index = {b: i for i, b in enumerate(buckets)}
    n = len(buckets)

    cam = [CountSample.camera_id == camera_id] if camera_id else []
    counts: dict[str, dict[str, list]] = {}
    sums: dict[str, list[float]] = {}
    minutes: dict[str, list[int]] = {}
    for b_start, cls, avg, peak in db.execute(
        select(CountSample.bucket_start, CountSample.class_name, CountSample.avg_count, CountSample.max_count)
        .where(CountSample.bucket_start >= start, *cam)
    ):
        i = index.get(_floor(b_start, bucket))
        if i is None:
            continue
        c = counts.setdefault(cls, {"peak": [0] * n, "avg": [0.0] * n})
        c["peak"][i] = max(c["peak"][i], peak)
        sums.setdefault(cls, [0.0] * n)[i] += avg
        minutes.setdefault(cls, [0] * n)[i] += 1
    for cls, c in counts.items():
        # With several cameras this is the per-minute average summed across cameras.
        c["avg"] = [round(s / m, 2) if m else 0 for s, m in zip(sums[cls], minutes[cls])]

    ev = [Event.camera_id == camera_id] if camera_id else []
    events: dict[str, list[int]] = {}
    for occurred, etype in db.execute(select(Event.occurred_at, Event.event_type).where(Event.occurred_at >= start, *ev)):
        i = index.get(_floor(occurred, bucket))
        if i is not None:
            events.setdefault(etype, [0] * n)[i] += 1

    return {"bucket": bucket, "buckets": [b.isoformat() for b in buckets], "counts": counts, "events": events}


@router.get("/api/settings", response_model=SettingsOut)
def read_settings(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return get_settings(db)


@router.patch("/api/settings", response_model=SettingsOut)
def write_settings(body: SettingsUpdate, request: Request, admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    result = update_settings(db, changes)
    audit(db, request, admin, "settings.update", **changes)
    db.commit()
    return result


def _dir_size(path) -> tuple[int, int]:
    files = size = 0
    for p in path.rglob("*"):
        if p.is_file():
            files += 1
            size += p.stat().st_size
    return files, size


@router.get("/api/system")
def system(_: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        db.execute(text("select 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    storage = app_settings.event_storage_path
    files, size = _dir_size(storage)
    disk = shutil.disk_usage(storage)
    last = worker_contact.get("at")
    return {
        "database": "ok" if db_ok else "error",
        "worker": {
            "last_contact": last.isoformat() if last else None,
            "connected": bool(last and datetime.now(timezone.utc) - last < timedelta(seconds=30)),
        },
        "storage": {"snapshots": files, "snapshot_bytes": size, "disk_free_bytes": disk.free,
                    "disk_total_bytes": disk.total},
        "events_total": db.scalar(select(func.count()).select_from(Event)) or 0,
    }
