"""Deletes events, snapshots and count samples older than the configured retention."""

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import CountSample, Event, UserSession
from app.settings_store import get_settings

log = logging.getLogger(__name__)


def _remove_old_day_dirs(root: Path, cutoff: datetime) -> int:
    """Snapshots live in root/YYYY/MM/DD/. Remove whole day folders older than the cutoff."""
    removed = 0
    for day_dir in root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]"):
        try:
            day = datetime.strptime("/".join(day_dir.parts[-3:]), "%Y/%m/%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day + timedelta(days=1) <= cutoff:
            shutil.rmtree(day_dir, ignore_errors=True)
            removed += 1
    # Tidy up now-empty month/year folders.
    for d in sorted(root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]"), reverse=True) + sorted(root.glob("[0-9][0-9][0-9][0-9]")):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return removed


def run_retention(db: Session, storage: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cfg = get_settings(db)
    event_cutoff = now - timedelta(days=cfg["event_retention_days"])
    count_cutoff = now - timedelta(days=cfg["count_retention_days"])

    # Delete snapshot files of individual old events (covers events whose day folder is kept).
    old_snapshots = db.scalars(select(Event.snapshot_path).where(Event.occurred_at < event_cutoff,
                                                                 Event.snapshot_path.is_not(None))).all()
    root = storage.resolve()
    for rel in old_snapshots:
        path = (root / rel).resolve()
        if path.is_relative_to(root):
            path.unlink(missing_ok=True)

    events = db.execute(delete(Event).where(Event.occurred_at < event_cutoff)).rowcount
    counts = db.execute(delete(CountSample).where(CountSample.bucket_start < count_cutoff)).rowcount
    sessions = db.execute(delete(UserSession).where(UserSession.expires_at < now)).rowcount
    db.commit()
    day_dirs = _remove_old_day_dirs(root, event_cutoff)

    result = {"events": events, "count_samples": counts, "sessions": sessions, "snapshot_days": day_dirs}
    if any(result.values()):
        log.info("retention removed %s", result)
    return result
