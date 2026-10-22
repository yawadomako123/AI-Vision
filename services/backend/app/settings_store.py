"""Operator-editable settings, stored in the settings table (one JSON row per group) with code defaults."""

from sqlalchemy.orm import Session

from app.models import Setting

DEFAULTS = {
    "general": {
        "event_retention_days": 30,
        "count_retention_days": 90,
        "clips_enabled": True,
        "clip_retention_days": 7,
        "clip_storage_mb": 1024,
        "sighting_retention_days": 14,
        # Plain-English search costs ~0.5-0.7 GB RAM on the edge box, so it's opt-in ("Pro").
        "search_enabled": False,
        # Frames analysed per second per camera: lower = lighter on weak hardware.
        "analysis_fps": 3,
        # How sure the model must be before something counts: high = catches more, also more false boxes.
        "detection_sensitivity": "balanced",
        # Rest the AI on cameras where nothing moves (a safety check still runs every 2 s).
        "motion_skip": True,
    },
    "system": {
        "armed": True,
        "changed_by": None,
        "changed_at": None,
    },
    "notifications": {
        "enabled": False,
        "server": "https://ntfy.sh",
        "topic": "",
        "min_severity": "high",
        "include_snapshot": False,  # off by default: on a public server the image leaves this machine
        "dashboard_url": "",  # e.g. a Tailscale URL, used as the tap-through link
        "token_encrypted": None,  # optional access token for a protected server
        "cooldown_seconds": 60,
        "daily_summary": True,  # one message a day about the last 24 h (only when notifications are on)
        "summary_time": "08:00",  # local time on the edge box
        "summary_last_sent": None,  # local date, so a restart never sends it twice
    },
}


# Minimum model confidence per sensitivity level (tuned on real indoor phone-camera footage).
SENSITIVITY_CONFIDENCE = {"high": 0.40, "balanced": 0.50, "low": 0.60}


def get_settings(db: Session, group: str = "general") -> dict:
    row = db.get(Setting, group)
    return {**DEFAULTS[group], **(row.value if row else {})}


def update_settings(db: Session, changes: dict, group: str = "general") -> dict:
    current = get_settings(db, group)
    current.update({k: v for k, v in changes.items() if k in DEFAULTS[group]})
    row = db.get(Setting, group)
    if row is None:
        db.add(Setting(key=group, value=current))
    else:
        row.value = current
    return current
