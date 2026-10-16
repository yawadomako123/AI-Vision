import av
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crypto import decrypt, encrypt
from app.db import get_db
from app.live import hub
from app.models import Camera
from app.schemas import CameraCreate, CameraOut, CameraTestResult, CameraUpdate
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


@router.get("", response_model=list[CameraOut])
def list_cameras(db: Session = Depends(get_db)):
    return [camera_out(c) for c in db.scalars(select(Camera).order_by(Camera.id))]


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def create_camera(body: CameraCreate, db: Session = Depends(get_db)):
    camera = Camera(
        name=body.name, location=body.location, enabled=body.enabled,
        stream_url_encrypted=encrypt(body.stream_url),
    )
    db.add(camera)
    _commit(db)
    out = camera_out(camera)
    hub.broadcast_from_sync("camera", out.model_dump())
    return out


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, db: Session = Depends(get_db)):
    return camera_out(_get(db, camera_id))


@router.patch("/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: int, body: CameraUpdate, db: Session = Depends(get_db)):
    camera = _get(db, camera_id)
    changes = body.model_dump(exclude_unset=True)
    if "stream_url" in changes:
        camera.stream_url_encrypted = encrypt(changes.pop("stream_url"))
    for field, value in changes.items():
        setattr(camera, field, value)
    _commit(db)
    out = camera_out(camera)
    hub.broadcast_from_sync("camera", out.model_dump())
    return out


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, camera_id))
    db.commit()
    hub.broadcast_from_sync("camera_deleted", {"id": camera_id})


@router.post("/{camera_id}/test", response_model=CameraTestResult)
def test_camera(camera_id: int, db: Session = Depends(get_db)):
    """Try to open the stream and decode one frame (5 s timeout)."""
    url = decrypt(_get(db, camera_id).stream_url_encrypted)
    try:
        with av.open(url, options={"rtsp_transport": "tcp"}, timeout=(5.0, 5.0)) as container:
            stream = container.streams.video[0]
            frame = next(container.decode(stream))
            return CameraTestResult(ok=True, width=frame.width, height=frame.height, codec=stream.codec_context.name)
    except Exception as exc:  # noqa: BLE001
        # Never echo the URL back: FFmpeg errors can include it, with the password.
        message = str(exc).replace(url, "<stream>")
        return CameraTestResult(ok=False, error=message[:300])
