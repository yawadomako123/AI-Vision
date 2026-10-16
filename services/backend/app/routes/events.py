import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.audit import audit
from app.auth import current_user
from app.db import get_db
from app.live import hub
from app.models import Event, User
from app.schemas import EventOut, EventPage, EventUpdate
from app.serializers import event_out

router = APIRouter(prefix="/api/events", tags=["events"])


def event_filters(
    camera_id: int | None = None,
    event_type: str | None = None,
    status_: str | None = Query(default=None, alias="status"),
    severity: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list:
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
    return filters


@router.get("", response_model=EventPage)
def list_events(
    filters: list = Depends(event_filters),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count()).select_from(Event).where(*filters))
    rows = db.scalars(
        select(Event).options(joinedload(Event.camera)).where(*filters)
        .order_by(Event.occurred_at.desc(), Event.id.desc()).limit(limit).offset(offset)
    )
    return EventPage(total=total or 0, items=[event_out(e) for e in rows])


CSV_COLUMNS = ["id", "occurred_at", "camera", "event_type", "severity", "rule", "object_class", "track_id",
               "confidence", "status", "reviewed_by", "reviewed_at", "note", "snapshot_url"]
CSV_MAX_ROWS = 50_000


def _cell(value) -> str:
    text = "" if value is None else str(value)
    # Spreadsheet formula injection guard (notes are free text typed by users).
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@router.get("/export.csv")
def export_csv(filters: list = Depends(event_filters), request: Request = None,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Event).options(joinedload(Event.camera)).where(*filters)
        .order_by(Event.occurred_at.desc()).limit(CSV_MAX_ROWS)
    ).all()
    audit(db, request, user, "events.export", rows=len(rows))
    db.commit()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for e in rows:
        o = event_out(e)
        writer.writerow([_cell(v) for v in (
            o.id, o.occurred_at.isoformat(), o.camera_name, o.event_type, o.severity, o.rule_name,
            o.object_class, o.track_id, o.confidence, o.status, o.reviewed_by,
            o.reviewed_at.isoformat() if o.reviewed_at else None, o.note, o.snapshot_url,
        )])
    name = f"events-{datetime.now(timezone.utc):%Y%m%d-%H%M}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _get(db: Session, event_id: int) -> Event:
    event = db.get(Event, event_id, options=[joinedload(Event.camera)])
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return event


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)):
    return event_out(_get(db, event_id))


@router.patch("/{event_id}", response_model=EventOut)
def update_event(event_id: int, body: EventUpdate, request: Request, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    event = _get(db, event_id)
    changes = body.model_dump(exclude_unset=True, exclude={"reviewed_by"})
    for field, value in changes.items():
        setattr(event, field, value)
    if "status" in changes:
        reviewed = changes["status"] != "unreviewed"
        event.reviewed_at = datetime.now(timezone.utc) if reviewed else None
        event.reviewed_by = user.username if reviewed else None  # from the session, not the client
    audit(db, request, user, "event.review", f"event:{event.id}", **changes)
    db.commit()
    out = event_out(event)
    hub.broadcast_from_sync("event_updated", out.model_dump())
    return out
