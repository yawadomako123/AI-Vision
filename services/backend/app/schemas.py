from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EventStatus = Literal["unreviewed", "acknowledged", "resolved", "false_alarm"]
STREAM_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")


def _check_stream_url(v: str) -> str:
    v = v.strip()
    if not v.lower().startswith(STREAM_SCHEMES):
        raise ValueError(f"stream_url must start with one of {', '.join(STREAM_SCHEMES)}")
    return v


# --- Cameras -----------------------------------------------------------------

class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    stream_url: str = Field(max_length=2000)
    location: str | None = Field(default=None, max_length=200)
    enabled: bool = True

    _url = field_validator("stream_url")(_check_stream_url)


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    stream_url: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None

    @field_validator("stream_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return None if v is None else _check_stream_url(v)


class CameraOut(BaseModel):
    """Public view: the stream URL is masked, never the real password."""

    id: int
    name: str
    location: str | None
    stream_url_masked: str
    enabled: bool
    status: str
    last_seen_at: datetime | None
    infer_fps: float | None
    created_at: datetime


class CameraTestResult(BaseModel):
    ok: bool
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    error: str | None = None


# --- Events ------------------------------------------------------------------

class EventObject(BaseModel):
    class_: str | None = Field(default=None, alias="class")
    track_id: int | None = None
    confidence: float | None = None
    box: list[float] | None = None


class EventRule(BaseModel):
    id: str
    name: str
    type: str


class EventIn(BaseModel):
    """What the vision worker submits (same shape as its events.jsonl lines)."""

    id: str = Field(max_length=120)
    camera_id: int
    event_type: str = Field(max_length=60)
    occurred_at: datetime
    severity: str = "medium"
    rule: EventRule | None = None
    object: EventObject | None = None
    details: dict = {}
    evidence: dict | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    camera_id: int
    camera_name: str | None = None
    event_type: str
    severity: str
    occurred_at: datetime
    rule_id: str | None
    rule_name: str | None
    object_class: str | None
    track_id: int | None
    confidence: float | None
    details: dict
    snapshot_url: str | None = None
    status: str
    note: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None


class EventUpdate(BaseModel):
    status: EventStatus | None = None
    note: str | None = Field(default=None, max_length=2000)
    reviewed_by: str | None = Field(default=None, max_length=120)


class EventPage(BaseModel):
    total: int
    items: list[EventOut]


# --- Worker status -----------------------------------------------------------

class CameraStatusIn(BaseModel):
    status: Literal["online", "offline", "connecting"]
    last_frame_at: datetime | None = None
    infer_fps: float | None = None


class InternalCamera(BaseModel):
    """Worker view: includes the decrypted stream URL. Only on the service-token API."""

    id: int
    name: str
    stream_url: str
    enabled: bool
