from app.crypto import decrypt, mask_url
from app.models import Camera, Event
from app.schemas import CameraOut, EventOut


def camera_out(c: Camera) -> CameraOut:
    return CameraOut(
        id=c.id,
        name=c.name,
        location=c.location,
        stream_url_masked=mask_url(decrypt(c.stream_url_encrypted)),
        enabled=c.enabled,
        status=c.status,
        last_seen_at=c.last_seen_at,
        infer_fps=c.infer_fps,
        created_at=c.created_at,
        notify_muted_until=c.notify_muted_until,
        live_path=_live_path(c),
    )


def _live_path(c: Camera) -> str | None:
    from app.recording import camera_path, mediamtx

    cp = camera_path(c.id, decrypt(c.stream_url_encrypted))
    if cp is None or (cp.proxied and not mediamtx.enabled):
        return None
    return cp.name


def event_out(e: Event) -> EventOut:
    out = EventOut.model_validate(e)
    out.camera_name = e.camera.name if e.camera else None
    out.snapshot_url = f"/media/events/{e.snapshot_path}" if e.snapshot_path else None
    out.clip_url = f"/media/events/{e.clip_path}" if e.clip_path else None
    out.received_at = e.created_at
    return out
