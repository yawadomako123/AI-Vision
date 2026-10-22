"""Recordings: what's on disk for a camera, and playing a stretch of it (behind the dashboard login)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import current_user
from app.crypto import decrypt
from app.db import get_db
from app.models import Camera, User
from app.recording import MediaMTXUnavailable, camera_path, mediamtx

router = APIRouter(prefix="/api/cameras", tags=["recordings"])
MAX_CHUNK_SECONDS = 600


def _path(db: Session, camera_id: int) -> str:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")
    cp = camera_path(camera.id, decrypt(camera.stream_url_encrypted))
    if cp is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This camera's stream type can't be recorded (RTSP only).")
    return cp.name


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@router.get("/{camera_id}/recordings")
def list_recordings(camera_id: int, start: datetime, end: datetime, _: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """Recorded stretches overlapping [start, end): [{start, end}] in UTC, merged when contiguous."""
    path = _path(db, camera_id)
    start, end = _utc(start), _utc(end)
    try:
        raw = mediamtx.list_recordings(path)
    except MediaMTXUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    spans: list[list[datetime]] = []
    for seg in sorted(raw, key=lambda s: s["start"]):
        s = _utc(datetime.fromisoformat(seg["start"].replace("Z", "+00:00")))
        e = s + timedelta(seconds=seg["duration"])
        if e <= start or s >= end:
            continue
        if spans and (s - spans[-1][1]).total_seconds() <= 2:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
    return {"path": path, "segments": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in spans]}


@router.get("/{camera_id}/playback")
def playback(camera_id: int, start: datetime,
             duration: float = Query(default=300, gt=0, le=MAX_CHUNK_SECONDS),
             _: User = Depends(current_user), db: Session = Depends(get_db)):
    """An MP4 of the recording from `start`, up to `duration` seconds (gaps are skipped)."""
    path = _path(db, camera_id)
    try:
        upstream = mediamtx.open_playback(path, _utc(start).isoformat().replace("+00:00", "Z"), duration)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"playback unavailable ({exc})") from None
    if upstream.status_code != 200:
        upstream.close()
        upstream.extensions["owning_client"].close()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing was recorded at that time.")

    def body():
        try:
            yield from upstream.iter_bytes()
        finally:
            upstream.close()
            upstream.extensions["owning_client"].close()

    return StreamingResponse(body(), media_type="video/mp4", headers={"Cache-Control": "no-store"})
