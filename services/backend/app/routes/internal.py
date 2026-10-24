"""Endpoints for the vision worker only, guarded by the shared service token."""

import base64
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import decrypt
from app.db import get_db
from app.live import hub, live_previews, worker_contact
from app.notifier import notifier
from app.recording import worker_url
from app.settings_store import SENSITIVITY_CONFIDENCE, get_settings
from app.models import Camera, CountSample, DetectionRule, Event, LineCount
from app.schemas import (
    CameraStatusIn, CountSampleIn, EventIn, EventOut, InternalCamera, LineCountIn, LiveFrameIn, RuleOut,
)
from app.serializers import event_out


def require_service_token(x_service_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_service_token, settings.service_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid service token")
    worker_contact["at"] = datetime.now(timezone.utc)


router = APIRouter(prefix="/api/internal", tags=["internal"], dependencies=[Depends(require_service_token)])


@router.get("/cameras", response_model=list[InternalCamera])
def worker_cameras(db: Session = Depends(get_db)):
    return [
        InternalCamera(id=c.id, name=c.name, stream_url=worker_url(c.id, decrypt(c.stream_url_encrypted)),
                       enabled=c.enabled)
        for c in db.scalars(select(Camera).order_by(Camera.id))
    ]


@router.get("/rules", response_model=dict[int, list[RuleOut]])
def worker_rules(db: Session = Depends(get_db)):
    """Enabled rules grouped by camera id. While the system is disarmed only lines are sent, with
    alerts switched off, so they keep counting entries/exits but no rule can fire; camera health
    alerts are raised independently and keep working. Privacy masks always apply."""
    armed = get_settings(db, "system")["armed"]
    query = select(DetectionRule).where(DetectionRule.enabled).order_by(DetectionRule.id)
    if not armed:
        query = query.where(DetectionRule.type.in_(["line_crossing", "privacy_mask"]))
    grouped: dict[int, list[RuleOut]] = {}
    for rule in db.scalars(query):
        out = RuleOut.model_validate(rule)
        if not armed:
            out.alert = False
        grouped.setdefault(rule.camera_id, []).append(out)
    return grouped


@router.get("/config")
def worker_config(db: Session = Depends(get_db)):
    """Settings the worker applies at runtime (polled with the rules)."""
    cfg = get_settings(db)
    return {"clips": {"enabled": cfg["clips_enabled"]}, "search_enabled": cfg["search_enabled"],
            "analysis_fps": cfg["analysis_fps"], "live_cameras": sorted(hub.watched()),
            "detection_confidence": SENSITIVITY_CONFIDENCE.get(cfg["detection_sensitivity"], 0.5),
            "motion_skip": cfg["motion_skip"], "tamper_alerts": cfg["tamper_alerts"],
            "plates_enabled": cfg["plates_enabled"]}


@router.post("/live", status_code=status.HTTP_204_NO_CONTENT)
async def live_frame(body: LiveFrameIn):
    """Detection boxes (and now and then a small preview image) for a camera on the live wall."""
    preview = False
    if body.preview:
        try:
            jpeg = base64.b64decode(body.preview, validate=True)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "preview must be base64") from None
        if jpeg[:2] == b"\xff\xd8":
            live_previews[body.camera_id] = (jpeg, time.time())
            preview = True
    await hub.send_to_watchers(body.camera_id, "detections", {
        "camera_id": body.camera_id, "at": body.at, "width": body.width, "height": body.height,
        "objects": [o.model_dump() for o in body.objects], "preview": preview,
    })


@router.post("/counts", status_code=status.HTTP_204_NO_CONTENT)
def ingest_counts(samples: list[CountSampleIn], db: Session = Depends(get_db)):
    known = set(db.scalars(select(Camera.id)))
    db.add_all(CountSample(**s.model_dump()) for s in samples if s.camera_id in known)
    db.commit()


@router.post("/line-counts", status_code=status.HTTP_204_NO_CONTENT)
def ingest_line_counts(samples: list[LineCountIn], db: Session = Depends(get_db)):
    known = set(db.scalars(select(Camera.id)))
    rules = set(db.scalars(select(DetectionRule.id)))
    db.add_all(
        LineCount(**{**s.model_dump(), "rule_id": s.rule_id if s.rule_id in rules else None})
        for s in samples if s.camera_id in known
    )
    db.commit()


@router.post("/cameras/{camera_id}/status", status_code=status.HTTP_204_NO_CONTENT)
def report_status(camera_id: int, body: CameraStatusIn, db: Session = Depends(get_db)):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")
    changed = camera.status != body.status
    camera.status = body.status
    if body.last_frame_at:
        camera.last_seen_at = body.last_frame_at
    camera.infer_fps = body.infer_fps
    db.commit()
    if changed:
        hub.broadcast_from_sync("camera_status", {
            "id": camera.id, "status": camera.status, "last_seen_at": camera.last_seen_at,
        })


@router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def ingest_event(body: EventIn, db: Session = Depends(get_db)):
    existing = db.scalar(select(Event).where(Event.external_id == body.id))
    if existing is not None:  # worker retry: already stored
        return event_out(existing)
    if db.get(Camera, body.camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")

    obj = body.object
    event = Event(
        external_id=body.id,
        camera_id=body.camera_id,
        event_type=body.event_type,
        severity=body.severity,
        occurred_at=body.occurred_at,
        rule_id=body.rule.id if body.rule else None,
        rule_name=body.rule.name if body.rule else None,
        object_class=obj.class_ if obj else None,
        track_id=obj.track_id if obj else None,
        confidence=obj.confidence if obj else None,
        details={**body.details, **({"box": obj.box} if obj and obj.box else {})},
        snapshot_path=(body.evidence or {}).get("snapshot_path"),
        clip_path=(body.evidence or {}).get("clip_path"),
    )
    return publish_event(db, event)


def publish_event(db: Session, event: Event) -> EventOut:
    """Store a new event, push it to open dashboards and notify the phone (unless muted)."""
    db.add(event)
    db.commit()
    db.refresh(event)
    out = event_out(event)
    hub.broadcast_from_sync("event", out.model_dump())
    muted = event.camera.notify_muted_until
    if muted and muted.tzinfo is None:  # SQLite (tests) returns naive datetimes
        muted = muted.replace(tzinfo=timezone.utc)
    if not (muted and muted > datetime.now(timezone.utc)):  # "Mute camera 1 h" from a notification
        notifier.notify({**out.model_dump(), "snapshot_path": event.snapshot_path}, get_settings(db, "notifications"))
    return out
