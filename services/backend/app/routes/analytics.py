import csv
import io
import shutil
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import current_user, require_admin
from app.config import settings as app_settings
from app.crypto import encrypt
from app.db import get_db
from app.live import hub, worker_contact
from app.models import Camera, CountSample, Event, LineCount, User
from app.notifier import send as notifier_send
from app.routes.events import _cell
from app.schemas import (
    ArmingIn, ArmingOut, NotificationSettingsOut, NotificationSettingsUpdate, NotificationTestResult, SettingsOut, SettingsUpdate,
)
from app.settings_store import get_settings, update_settings
from app.watchdog import watchdog

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


def _buckets(hours: int, bucket: str) -> tuple[list[datetime], dict[datetime, int]]:
    """Bucket starts covering the last `hours` up to now, and bucket start -> index."""
    now = datetime.now(timezone.utc)
    step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    t = _floor(now - timedelta(hours=hours), bucket) + step
    buckets = []
    while t <= now:
        buckets.append(t)
        t += step
    return buckets, {b: i for i, b in enumerate(buckets)}


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
    buckets, index = _buckets(hours, bucket)
    start, n = buckets[0], len(buckets)

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


def _entry_rows(db: Session, start: datetime, camera_id: int | None):
    cam = [LineCount.camera_id == camera_id] if camera_id else []
    return db.execute(
        select(LineCount.camera_id, Camera.name, LineCount.rule_id, LineCount.rule_name, LineCount.bucket_start,
               LineCount.class_name, LineCount.in_count, LineCount.out_count)
        .join(Camera, Camera.id == LineCount.camera_id)
        .where(LineCount.bucket_start >= start, *cam)
    ).all()


@router.get("/api/analytics/entries")
def entries(
    hours: int = Query(default=24, ge=1, le=24 * 31),
    bucket: Literal["hour", "day"] = "hour",
    camera_id: int | None = None,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Entry/exit totals from counting lines: per line, per class, and over time."""
    buckets, index = _buckets(hours, bucket)
    n = len(buckets)
    lines: dict[tuple, dict] = {}
    total = {"in": 0, "out": 0}
    for cam_id, cam_name, rule_id, rule_name, b_start, cls, n_in, n_out in _entry_rows(db, buckets[0], camera_id):
        i = index.get(_floor(b_start, bucket))
        if i is None:
            continue
        # A deleted rule's counts stay grouped under its name.
        line = lines.setdefault((cam_id, rule_id if rule_id is not None else rule_name), {
            "camera_id": cam_id, "camera_name": cam_name, "rule_id": rule_id, "rule_name": rule_name,
            "in_total": 0, "out_total": 0, "by_class": {}, "in": [0] * n, "out": [0] * n})
        line["in"][i] += n_in
        line["out"][i] += n_out
        line["in_total"] += n_in
        line["out_total"] += n_out
        c = line["by_class"].setdefault(cls, {"in": 0, "out": 0})
        c["in"] += n_in
        c["out"] += n_out
        total["in"] += n_in
        total["out"] += n_out
    return {"bucket": bucket, "buckets": [b.isoformat() for b in buckets], "total": total,
            "lines": sorted(lines.values(), key=lambda l: (l["camera_name"], l["rule_name"]))}


@router.get("/api/analytics/entries.csv")
def entries_csv(
    hours: int = Query(default=24, ge=1, le=24 * 31),
    bucket: Literal["hour", "day"] = "hour",
    camera_id: int | None = None,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """One row per time bucket, camera, line and class."""
    buckets, _index = _buckets(hours, bucket)
    rows: dict[tuple, list[int]] = {}
    for _cam, cam_name, _rid, rule_name, b_start, cls, n_in, n_out in _entry_rows(db, buckets[0], camera_id):
        r = rows.setdefault((_floor(b_start, bucket), cam_name, rule_name, cls), [0, 0])
        r[0] += n_in
        r[1] += n_out
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"{bucket}_start_utc", "camera", "line", "class", "in", "out"])
    for (b, cam_name, rule_name, cls), (n_in, n_out) in sorted(rows.items()):
        writer.writerow([_cell(v) for v in (b.isoformat(), cam_name, rule_name, cls, n_in, n_out)])
    name = f"entries-{datetime.now(timezone.utc):%Y%m%d-%H%M}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/analytics/rules")
def rule_performance(hours: int = Query(default=24 * 7, ge=1, le=24 * 90), _: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    """Per rule: how many alerts and how they were reviewed. The false-alarm rate is the number
    to watch during a pilot: it tells you which rules need a smaller zone, a higher minimum
    confidence or a longer cooldown."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(Event.camera_id, Camera.name, Event.rule_id, Event.rule_name, Event.event_type, Event.status, func.count())
        .join(Camera, Camera.id == Event.camera_id)
        .where(Event.occurred_at >= since, Event.rule_id.is_not(None))
        .group_by(Event.camera_id, Camera.name, Event.rule_id, Event.rule_name, Event.event_type, Event.status)
    ).all()
    rules: dict[tuple, dict] = {}
    for cam_id, cam_name, rule_id, rule_name, etype, status_, n in rows:
        r = rules.setdefault((cam_id, rule_id), {
            "camera_id": cam_id, "camera_name": cam_name, "rule_id": rule_id, "rule_name": rule_name,
            "event_type": etype, "total": 0, "unreviewed": 0, "acknowledged": 0, "resolved": 0, "false_alarm": 0,
        })
        r["total"] += n
        r[status_] = r.get(status_, 0) + n
        r["rule_name"] = rule_name or r["rule_name"]
    out = []
    for r in rules.values():
        reviewed = r["total"] - r["unreviewed"]
        r["false_alarm_rate"] = round(r["false_alarm"] / reviewed, 3) if reviewed else None
        out.append(r)
    return sorted(out, key=lambda r: (-r["total"], r["rule_name"] or ""))


@router.get("/api/arming", response_model=ArmingOut)
def read_arming(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return get_settings(db, "system")


@router.put("/api/arming", response_model=ArmingOut)
def write_arming(body: ArmingIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Any signed-in operator can arm/disarm (e.g. a guard starting a shift). Always audited."""
    state = update_settings(db, {"armed": body.armed, "changed_by": user.username,
                                 "changed_at": datetime.now(timezone.utc).isoformat()}, "system")
    audit(db, request, user, "system.arm" if body.armed else "system.disarm")
    db.commit()
    hub.broadcast_from_sync("arming", state)
    return state


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


def _notification_out(cfg: dict) -> NotificationSettingsOut:
    return NotificationSettingsOut(**{k: v for k, v in cfg.items() if k != "token_encrypted"},
                                   has_token=bool(cfg.get("token_encrypted")))


@router.get("/api/settings/notifications", response_model=NotificationSettingsOut)
def read_notifications(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    # Admin-only: on a public ntfy server, anyone who knows the topic can read the alerts.
    return _notification_out(get_settings(db, "notifications"))


@router.patch("/api/settings/notifications", response_model=NotificationSettingsOut)
def write_notifications(body: NotificationSettingsUpdate, request: Request, admin: User = Depends(require_admin),
                        db: Session = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    logged = {k: v for k, v in changes.items() if k not in ("token", "topic")}
    if "token" in changes:
        token = changes.pop("token")
        changes["token_encrypted"] = encrypt(token) if token else None
        logged["token"] = "set" if token else "cleared"
    if "topic" in changes:
        logged["topic"] = "changed"
    cfg = update_settings(db, changes, "notifications")
    audit(db, request, admin, "settings.notifications", **logged)
    db.commit()
    return _notification_out(cfg)


@router.post("/api/settings/notifications/test", response_model=NotificationTestResult)
def test_notification(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = get_settings(db, "notifications")
    if not cfg.get("topic"):
        return NotificationTestResult(ok=False, error="set a topic first")
    msg = {"title": "AI Vision Edge test", "message": "Notifications are working.", "priority": 3,
           "tags": ["white_check_mark"]}
    try:
        notifier_send(cfg, msg)
    except httpx.HTTPError as exc:
        return NotificationTestResult(ok=False, error=str(exc)[:300])
    return NotificationTestResult(ok=True)


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
        "problems": watchdog.status(),
    }
