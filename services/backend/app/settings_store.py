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
    },
}


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
