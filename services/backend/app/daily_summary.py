"""Daily summary push: one short message a day about the last 24 hours.

"Yesterday: 12 alerts (3 to review), 214 in / 209 out, peak 6 people. All 3 cameras online."

Sent at the chosen local time (Settings -> Phone notifications) when phone notifications are
on. The date it was last sent is stored, so restarting the API never sends it twice.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Camera, CountSample, Event, LineCount
from app.notifier import LABELS
from app.settings_store import get_settings, update_settings

log = logging.getLogger(__name__)


def build_summary(db: Session, now: datetime | None = None, problems: list[dict] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    by_type = dict(db.execute(
        select(Event.event_type, func.count()).where(Event.occurred_at >= since).group_by(Event.event_type)).all())
    INFO = ("camera_offline", "camera_online", "camera_tamper_cleared")
    alerts = sum(n for t, n in by_type.items() if t not in INFO)
    to_review = db.scalar(select(func.count()).select_from(Event).where(
        Event.occurred_at >= since, Event.status == "unreviewed",
        Event.event_type.not_in(INFO))) or 0
    false_alarms = db.scalar(select(func.count()).select_from(Event).where(
        Event.occurred_at >= since, Event.status == "false_alarm")) or 0
    peak_people = db.scalar(select(func.max(CountSample.max_count)).where(
        CountSample.bucket_start >= since, CountSample.class_name == "person")) or 0
    entries = db.execute(select(func.coalesce(func.sum(LineCount.in_count), 0), func.coalesce(func.sum(LineCount.out_count), 0))
                         .where(LineCount.bucket_start >= since)).one()
    cams = db.execute(select(Camera.status, func.count()).where(Camera.enabled).group_by(Camera.status)).all()
    total = sum(n for _, n in cams)
    online = sum(n for s, n in cams if s == "online")
    outages = by_type.get("camera_offline", 0)

    parts = []
    if alerts:
        top = sorted(((n, t) for t, n in by_type.items() if t not in INFO), reverse=True)[:3]
        kinds = ", ".join(f"{n} {LABELS.get(t, t).lower()}" for n, t in top)
        parts.append(f"{alerts} alert{'s' if alerts != 1 else ''} ({kinds})"
                     + (f", {to_review} to review" if to_review else ", all reviewed")
                     + (f", {false_alarms} false alarm{'s' if false_alarms != 1 else ''}" if false_alarms else ""))
    else:
        parts.append("No alerts")
    if entries[0] or entries[1]:
        parts.append(f"{entries[0]} in / {entries[1]} out")
    if peak_people:
        parts.append(f"at most {peak_people} {'person' if peak_people == 1 else 'people'} at once")
    health = (f"All {total} camera{'s' if total != 1 else ''} online." if total and online == total
              else f"{online} of {total} cameras online." if total else "No cameras set up.")
    if outages:
        health += f" {outages} camera outage{'s' if outages != 1 else ''}."
    if problems:
        health += " Needs attention: " + "; ".join(p["title"].lower() for p in problems) + "."
    return {
        "title": "Daily summary",
        "message": f"Last 24 h: {'. '.join(parts)}. {health}",
        "priority": 3 if (to_review or problems or (total and online < total)) else 2,
        "tags": ["bar_chart"],
    }


def due(cfg: dict, now_local: datetime) -> bool:
    """Is today's summary due (time reached and not sent yet today)?"""
    if not (cfg.get("enabled") and cfg.get("topic") and cfg.get("daily_summary")):
        return False
    hh, mm = (int(x) for x in cfg.get("summary_time", "08:00").split(":"))
    if (now_local.hour, now_local.minute) < (hh, mm):
        return False
    return cfg.get("summary_last_sent") != now_local.date().isoformat()


def maybe_send(db: Session, notifier, problems: list[dict], now_local: datetime | None = None) -> bool:
    now_local = now_local or datetime.now()
    cfg = get_settings(db, "notifications")
    if not due(cfg, now_local):
        return False
    msg = build_summary(db, problems=problems)
    if cfg.get("dashboard_url"):
        msg["click"] = f"{cfg['dashboard_url'].rstrip('/')}/analytics"
    if notifier.notify_system(cfg, msg["title"], msg["message"], priority=msg["priority"], tags=msg["tags"]):
        update_settings(db, {"summary_last_sent": now_local.date().isoformat()}, "notifications")
        db.commit()
        log.info("daily summary sent")
        return True
    return False
