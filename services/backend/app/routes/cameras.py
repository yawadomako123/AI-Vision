import asyncio
import copy
import io
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import av
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import discovery
from app.recording import camera_path, delete_recordings, sync_recording
from app.audit import audit
from app.config import settings as app_settings
from app.auth import current_user, require_admin
from app.crypto import decrypt, encrypt, mask_url
from app.db import get_db
from app.live import hub, latest_preview
from app.models import Camera, User
from app.schemas import (
    CameraConnectIn, CameraCreate, CameraMuteIn, CameraOut, CameraTestResult, CameraUpdate, OnvifStreamsIn,
    StreamProbeIn,
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
    if url.lower().startswith(("http://", "https://")):
        # HLS web streams load a playlist and a first segment before any frame: allow longer.
        # Start a few segments behind live, like the worker (much faster to the first frame).
        return av.open(url, options={"live_start_index": "-6"}, timeout=(30.0, 30.0))
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
def create_camera(body: CameraCreate, request: Request, background: BackgroundTasks,
                  admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    camera = Camera(
        name=body.name, location=body.location, enabled=body.enabled,
        stream_url_encrypted=encrypt(body.stream_url), mac=body.mac.upper() if body.mac else None,
    )
    db.add(camera)
    audit(db, request, admin, "camera.create", f"camera:{body.name}", stream=mask_url(body.stream_url))
    _commit(db)
    out = camera_out(camera)
    hub.broadcast_from_sync("camera", out.model_dump())
    background.add_task(sync_recording)  # set up its MediaMTX proxy/recording now, not in 30 s
    return out


@router.get("/phone-setup")
def phone_setup(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Everything a phone needs to stream to this computer as the next free camera (cam1, cam2…),
    including a Larix Grove link that the dashboard shows as a QR code."""
    password = app_settings.mediamtx_publish_password
    ip = discovery.primary_ipv4()
    if not password or not ip:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Phone cameras aren't available: this computer isn't on a network, or the camera hub "
                            "has no publish password.")
    used = set()
    for cam in db.scalars(select(Camera)):
        parts = urlsplit(decrypt(cam.stream_url_encrypted))
        if parts.hostname in ("127.0.0.1", "localhost"):
            used.add(parts.path.strip("/"))
    path = next(f"cam{n}" for n in range(1, 1000) if f"cam{n}" not in used)
    port = app_settings.mediamtx_rtsp.rpartition(":")[2] or "8554"
    publish_url = f"rtsp://{ip}:{port}/{path}"
    # https://softvelum.com/larix/grove/ : Larix imports the connection after the user approves.
    grove = [("conn[][url]", publish_url), ("conn[][name]", f"AI Vision Edge {path}"),
             ("conn[][user]", "publisher"), ("conn[][pass]", password), ("conn[][overwrite]", "on"),
             ("conn[][active]", "on"), ("enc[vid][res]", "640x480"), ("enc[vid][fps]", "15"),
             ("enc[vid][bitrate]", "1000")]
    audit(db, request, admin, "camera.phone_setup", path=path)
    db.commit()
    return {"path": path, "publish_url": publish_url, "username": "publisher", "password": password,
            "read_url": f"rtsp://127.0.0.1:{port}/{path}",
            # Windows blocks phones on a network it calls Public: the dashboard explains the one setting.
            "network_public": discovery.network_category(ip) == "Public",
            "larix_url": "larix://set/v1?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in grove)}


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)):
    return camera_out(_get(db, camera_id))


@router.patch("/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: int, body: CameraUpdate, request: Request, background: BackgroundTasks,
                  admin: User = Depends(require_admin), db: Session = Depends(get_db)):
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
    background.add_task(sync_recording)
    return out


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: int, request: Request, background: BackgroundTasks,
                  admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    camera = _get(db, camera_id)
    cp = camera_path(camera.id, decrypt(camera.stream_url_encrypted))
    audit(db, request, admin, "camera.delete", f"camera:{camera.name}")
    db.delete(camera)
    db.commit()
    hub.broadcast_from_sync("camera_deleted", {"id": camera_id})
    background.add_task(sync_recording)
    # Its recordings go with it, unless another camera still uses the same MediaMTX path.
    others = {camera_path(c.id, decrypt(c.stream_url_encrypted)) for c in db.scalars(select(Camera))}
    if cp is not None and cp.name not in {o.name for o in others if o}:
        background.add_task(delete_recordings, cp.name)


def _redact(exc: Exception, url: str) -> str:
    # Never echo the URL back: FFmpeg errors can include it, with the password.
    return str(exc).replace(url, "<stream>")[:300]


# Discovery is a sweep of the whole local network: one at a time. A request that arrives while one
# runs (a double click, a second browser tab) waits for it and shares its result.
_discovery_lock = asyncio.Lock()
_last_discovery: tuple[float, dict] | None = None


@router.post("/discover")
async def discover_cameras(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Find cameras on the local network (ONVIF + RTSP sweep + makers by hardware address, ~3-5 s)."""
    global _last_discovery
    async with _discovery_lock:
        if _last_discovery and time.monotonic() - _last_discovery[0] < 3:
            result = copy.deepcopy(_last_discovery[1])
        else:
            result = await discovery.discover()
            _last_discovery = (time.monotonic(), copy.deepcopy(result))
    added = {urlsplit(decrypt(c.stream_url_encrypted)).hostname for c in db.scalars(select(Camera))}
    for dev in result["devices"]:
        # This computer's hub holds many phones (cam1, cam2…): the wizard checks each stream instead.
        dev["already_added"] = dev["ip"] in added and dev["ip"] != "127.0.0.1"
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


@router.post("/discover/connect")
def connect_camera(body: CameraConnectIn, request: Request, admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Check a found camera's login and return its ready-to-add streams (every camera of a recorder).
    Failures come back as {"ok": false, "code", "message"} in plain words."""
    try:
        result = discovery.connect(body.ip, body.brand, body.username, body.password, body.port, body.onvif_url)
    except discovery.ConnectError as exc:
        audit(db, request, admin, "camera.connect", ip=body.ip, ok=False, code=exc.code)
        db.commit()
        return {"ok": False, "code": exc.code, "message": str(exc)}
    audit(db, request, admin, "camera.connect", ip=body.ip, ok=True, streams=len(result["streams"]))
    db.commit()
    return {"ok": True, **result}


@router.post("/discover/snapshot", responses={200: {"content": {"image/jpeg": {}}}})
def stream_snapshot(body: StreamProbeIn, _: User = Depends(require_admin)):
    """A small picture from a stream that isn't added yet, so people can see which camera is which."""
    host = urlsplit(body.stream_url).hostname or ""
    if not discovery.is_local(host) and host != "localhost":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only cameras on the local network")
    try:
        with _open(body.stream_url) as container:
            _, frame = _first_frame(container)
            image = frame.to_image()
            image.thumbnail((480, 270))
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=80)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"no picture: {_redact(exc, body.stream_url)}")
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
