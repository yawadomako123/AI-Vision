"""Deletes events, snapshots and count samples older than the configured retention."""

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models import CountSample, Event, Sighting, UserSession
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

    root = storage.resolve()

    def unlink(rel: str) -> int:
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return 0
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        return size

    # Delete files of old events (covers events whose day folder is kept).
    old_files = db.execute(select(Event.snapshot_path, Event.clip_path).where(Event.occurred_at < event_cutoff)).all()
    for snap, clip in old_files:
        for rel in (snap, clip):
            if rel:
                unlink(rel)

    # Clips have their own, shorter retention and a total size cap (oldest go first). Several
    # alerts can share one clip file, so work per file and clear the link on every event using it.
    clips_removed = 0
    clip_cutoff = now - timedelta(days=cfg["clip_retention_days"])
    expired = set(db.scalars(select(Event.clip_path).where(Event.clip_path.is_not(None), Event.occurred_at < clip_cutoff)))
    cap = cfg["clip_storage_mb"] * 1024 * 1024
    kept = db.execute(
        select(Event.clip_path, func.min(Event.occurred_at)).where(Event.clip_path.is_not(None))
        .group_by(Event.clip_path).order_by(func.min(Event.occurred_at).desc())
    ).all()
    total = 0
    for rel, _ in kept:  # newest first: everything past the cap is removed
        if rel in expired:
            continue
        path = (root / rel).resolve()
        size = path.stat().st_size if path.is_relative_to(root) and path.is_file() else 0
        if total + size > cap:
            expired.add(rel)
        else:
            total += size
    for rel in expired:
        unlink(rel)
        clips_removed += 1
    if expired:
        db.execute(update(Event).where(Event.clip_path.in_(expired)).values(clip_path=None))

    # Sightings (search): delete their two small images, then the rows.
    sighting_cutoff = now - timedelta(days=cfg["sighting_retention_days"])
    old_sightings = db.execute(select(Sighting.thumbnail_path, Sighting.context_path)
                               .where(Sighting.first_seen < sighting_cutoff)).all()
    for thumb, ctx in old_sightings:
        unlink(thumb)
        unlink(ctx)
    sightings = db.execute(delete(Sighting).where(Sighting.first_seen < sighting_cutoff)).rowcount

    events = db.execute(delete(Event).where(Event.occurred_at < event_cutoff)).rowcount
    counts = db.execute(delete(CountSample).where(CountSample.bucket_start < count_cutoff)).rowcount
    sessions = db.execute(delete(UserSession).where(UserSession.expires_at < now)).rowcount
    db.commit()
    day_dirs = _remove_old_day_dirs(root, event_cutoff)

    result = {"events": events, "count_samples": counts, "sessions": sessions, "snapshot_days": day_dirs,
              "clips": clips_removed, "sightings": sightings}
    if any(result.values()):
        log.info("retention removed %s", result)
    return result
