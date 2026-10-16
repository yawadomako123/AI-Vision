"""Operator-editable settings, stored in the settings table with code defaults."""

from sqlalchemy.orm import Session

from app.models import Setting

DEFAULTS = {
    "event_retention_days": 30,
    "count_retention_days": 90,
}


def get_settings(db: Session) -> dict:
    row = db.get(Setting, "general")
    return {**DEFAULTS, **(row.value if row else {})}


def update_settings(db: Session, changes: dict) -> dict:
    current = get_settings(db)
    current.update({k: v for k, v in changes.items() if k in DEFAULTS and v is not None})
    row = db.get(Setting, "general")
    if row is None:
        db.add(Setting(key="general", value=current))
    else:
        row.value = current
    return current
