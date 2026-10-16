"""Endpoints for the vision worker only, guarded by the shared service token."""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import decrypt
from app.db import get_db
from app.live import hub
from app.models import Camera, Event
from app.schemas import CameraStatusIn, EventIn, EventOut, InternalCamera
from app.serializers import event_out


def require_service_token(x_service_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_service_token, settings.service_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid service token")


router = APIRouter(prefix="/api/internal", tags=["internal"], dependencies=[Depends(require_service_token)])


@router.get("/cameras", response_model=list[InternalCamera])
def worker_cameras(db: Session = Depends(get_db)):
    return [
        InternalCamera(id=c.id, name=c.name, stream_url=decrypt(c.stream_url_encrypted), enabled=c.enabled)
        for c in db.scalars(select(Camera).order_by(Camera.id))
    ]


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
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    out = event_out(event)
    hub.broadcast_from_sync("event", out.model_dump())
    return out
