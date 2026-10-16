from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.live import hub
from app.models import Event
from app.schemas import EventOut, EventPage, EventUpdate
from app.serializers import event_out

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=EventPage)
def list_events(
    camera_id: int | None = None,
    event_type: str | None = None,
    status_: str | None = Query(default=None, alias="status"),
    severity: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    filters = []
    if camera_id is not None:
        filters.append(Event.camera_id == camera_id)
    if event_type:
        filters.append(Event.event_type == event_type)
    if status_:
        filters.append(Event.status == status_)
    if severity:
        filters.append(Event.severity == severity)
    if since:
        filters.append(Event.occurred_at >= since)
    if until:
        filters.append(Event.occurred_at < until)

    total = db.scalar(select(func.count()).select_from(Event).where(*filters))
    rows = db.scalars(
        select(Event).options(joinedload(Event.camera)).where(*filters)
        .order_by(Event.occurred_at.desc(), Event.id.desc()).limit(limit).offset(offset)
    )
    return EventPage(total=total or 0, items=[event_out(e) for e in rows])


def _get(db: Session, event_id: int) -> Event:
    event = db.get(Event, event_id, options=[joinedload(Event.camera)])
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return event


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: int, db: Session = Depends(get_db)):
    return event_out(_get(db, event_id))


@router.patch("/{event_id}", response_model=EventOut)
def update_event(event_id: int, body: EventUpdate, db: Session = Depends(get_db)):
    event = _get(db, event_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(event, field, value)
    if "status" in changes:
        event.reviewed_at = datetime.now(timezone.utc) if changes["status"] != "unreviewed" else None
    db.commit()
    out = event_out(event)
    hub.broadcast_from_sync("event_updated", out.model_dump())
    return out
