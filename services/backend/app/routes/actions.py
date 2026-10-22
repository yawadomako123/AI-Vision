"""One-tap notification buttons (see app/actions.py). No login: the signed token is the permission."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.actions import MUTE_MINUTES, ExpiredToken, InvalidToken, read_token
from app.audit import audit
from app.auth import CSRF_HEADER, CSRF_VALUE
from app.db import get_db
from app.live import hub
from app.models import Camera, Event
from app.serializers import camera_out, event_out

router = APIRouter(tags=["actions"])
PHONE = "phone (notification button)"


@router.post("/api/actions/{token}")
def run_action(token: str, request: Request, db: Session = Depends(get_db)):
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")
    try:
        action, target = read_token(token)
    except ExpiredToken:
        raise HTTPException(status.HTTP_410_GONE, "This button has expired. Open the app instead.") from None
    except InvalidToken:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid action") from None

    now = datetime.now(timezone.utc)
    if action == "ack":
        event = db.get(Event, target)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        if event.status == "unreviewed":
            event.status, event.reviewed_by, event.reviewed_at = "acknowledged", PHONE, now
        audit(db, request, PHONE, "event.review", f"event:{event.id}", status=event.status, via="notification")
        db.commit()
        hub.broadcast_from_sync("event_updated", event_out(event).model_dump())
        return {"ok": True, "message": "Alert acknowledged."}

    camera = db.get(Camera, target)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")
    camera.notify_muted_until = now + timedelta(minutes=MUTE_MINUTES)
    audit(db, request, PHONE, "camera.mute", f"camera:{camera.name}", minutes=MUTE_MINUTES, via="notification")
    db.commit()
    hub.broadcast_from_sync("camera", camera_out(camera).model_dump())
    return {"ok": True, "message": f"Notifications from {camera.name} muted for {MUTE_MINUTES} minutes."}
