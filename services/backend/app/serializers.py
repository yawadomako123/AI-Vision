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
    )


def event_out(e: Event) -> EventOut:
    out = EventOut.model_validate(e)
    out.camera_name = e.camera.name if e.camera else None
    out.snapshot_url = f"/media/events/{e.snapshot_path}" if e.snapshot_path else None
    return out
