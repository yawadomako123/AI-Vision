import asyncio
import io
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import av
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import discovery
from app.audit import audit
from app.auth import current_user, require_admin
from app.crypto import decrypt, encrypt, mask_url
from app.db import get_db
from app.live import hub, latest_preview
from app.models import Camera, User
from app.schemas import (
    CameraCreate, CameraMuteIn, CameraOut, CameraTestResult, CameraUpdate, OnvifStreamsIn, StreamProbeIn,
)
from app.serializers import camera_out

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


def _get(db: Session, camera_id: int) -> Camera:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")
    return camera


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "a camera with that name already exists")


def _open(url: str):
    return av.open(url, options={"rtsp_transport": "tcp"}, timeout=(5.0, 5.0))


def _first_frame(container, budget_seconds: float = 8.0):
    """Decode the first complete frame. Joining a live stream mid-GOP yields packets that
    can't be decoded until the next keyframe; skip those instead of failing."""
    stream = container.streams.video[0]
    deadline = time.monotonic() + budget_seconds
    for packet in container.demux(stream):
        try:
            frames = packet.decode()
        except av.error.InvalidDataError:
            frames = []
        if frames:
            return stream, frames[0]
        if time.monotonic() > deadline:
            break
    raise RuntimeError("no decodable frame received")


@router.get("", response_model=list[CameraOut])
def list_cameras(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return [camera_out(c) for c in db.scalars(select(Camera).order_by(Camera.id))]


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def create_camera(body: CameraCreate, request: Request, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    camera = Camera(
        name=body.name, location=body.location, enabled=body.enabled,
        stream_url_encrypted=encrypt(body.stream_url),
    )
    db.add(camera)
    audit(db, request, admin, "camera.create", f"camera:{body.name}", stream=mask_url(body.stream_url))
    _commit(db)
    out = camera_out(camera)
    hub.broadcast_from_sync("camera", out.model_dump())
    return out


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)):
    return camera_out(_get(db, camera_id))


@router.patch("/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: int, body: CameraUpdate, request: Request, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    camera = _get(db, camera_id)
    changes = body.model_dump(exclude_unset=True)
    logged = {k: v for k, v in changes.items() if k != "stream_url"}
    if "stream_url" in changes:
        url = changes.pop("stream_url")
        camera.stream_url_encrypted = encrypt(url)
        logged["stream"] = mask_url(url)
    for field, value in changes.items():
        setattr(camera, field, value)
    audit(db, request, admin, "camera.update", f"camera:{camera.name}", **logged)
    _commit(db)
    out = camera_out(camera)
    hub.broadcast_from_sync("camera", out.model_dump())
    return out


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: int, request: Request, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    camera = _get(db, camera_id)
    audit(db, request, admin, "camera.delete", f"camera:{camera.name}")
    db.delete(camera)
    db.commit()
    hub.broadcast_from_sync("camera_deleted", {"id": camera_id})


def _redact(exc: Exception, url: str) -> str:
    # Never echo the URL back: FFmpeg errors can include it, with the password.
    return str(exc).replace(url, "<stream>")[:300]


# Discovery is a sweep of the whole local network: one at a time is plenty.
_discovery_lock = asyncio.Lock()


@router.post("/discover")
async def discover_cameras(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Find cameras on the local network (ONVIF + RTSP port sweep, ~5 s)."""
    if _discovery_lock.locked():
        raise HTTPException(status.HTTP_409_CONFLICT, "A search is already running. Try again in a few seconds.")
    async with _discovery_lock:
        result = await discovery.discover()
    added = {urlsplit(decrypt(c.stream_url_encrypted)).hostname for c in db.scalars(select(Camera))}
    for dev in result["devices"]:
        dev["already_added"] = dev["ip"] in added or (dev["ip"] == "127.0.0.1" and "localhost" in added)
    audit(db, request, admin, "camera.discover", found=len(result["devices"]))
    db.commit()
    return result


@router.post("/discover/onvif")
def onvif_streams(body: OnvifStreamsIn, admin: User = Depends(require_admin)):
    """Ask a discovered camera for its own stream addresses (needs its username and password)."""
    try:
        return {"streams": discovery.onvif_streams(body.onvif_url, body.username, body.password)}
    except discovery.OnvifError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None


@router.post("/probe", response_model=CameraTestResult)
def probe_stream(body: StreamProbeIn, _: User = Depends(require_admin)):
    """Test a stream URL before adding the camera."""
    try:
        with _open(body.stream_url) as container:
            stream, frame = _first_frame(container)
            return CameraTestResult(ok=True, width=frame.width, height=frame.height, codec=stream.codec_context.name)
    except Exception as exc:  # noqa: BLE001
        return CameraTestResult(ok=False, error=_redact(exc, body.stream_url))


@router.put("/{camera_id}/mute", response_model=CameraOut)
def mute_camera(camera_id: int, body: CameraMuteIn, request: Request, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    """Silence phone notifications from one camera for a while (0 = unmute). Operators may too,
    like arming: detection and recording carry on."""
    camera = _get(db, camera_id)
    camera.notify_muted_until = (datetime.now(timezone.utc) + timedelta(minutes=body.minutes)) if body.minutes else None
    audit(db, request, user, "camera.mute" if body.minutes else "camera.unmute", f"camera:{camera.name}",
          minutes=body.minutes)
    db.commit()
    out = camera_out(camera)
    hub.broadcast_from_sync("camera", out.model_dump())
    return out


@router.get("/{camera_id}/preview", responses={200: {"content": {"image/jpeg": {}}}})
def camera_preview(camera_id: int, _: User = Depends(current_user)):
    """Newest live-wall preview from the worker (only while the camera is being watched)."""
    jpeg = latest_preview(camera_id)
    if jpeg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no live preview yet")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/{camera_id}/test", response_model=CameraTestResult)
def test_camera(camera_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)):
    """Try to open the stream and decode one frame (5 s timeout)."""
    url = decrypt(_get(db, camera_id).stream_url_encrypted)
    try:
        with _open(url) as container:
            stream, frame = _first_frame(container)
            return CameraTestResult(ok=True, width=frame.width, height=frame.height, codec=stream.codec_context.name)
    except Exception as exc:  # noqa: BLE001
        return CameraTestResult(ok=False, error=_redact(exc, url))


@router.get("/{camera_id}/frame", responses={200: {"content": {"image/jpeg": {}}}})
def camera_frame(camera_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)):
    """One current JPEG frame, e.g. as the background for drawing rules."""
    url = decrypt(_get(db, camera_id).stream_url_encrypted)
    try:
        with _open(url) as container:
            _, frame = _first_frame(container)
            buf = io.BytesIO()
            frame.to_image().save(buf, format="JPEG", quality=85)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not grab a frame: {_redact(exc, url)}")
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
