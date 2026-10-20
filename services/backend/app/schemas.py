from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class StreamProbeIn(BaseModel):
    stream_url: str = Field(max_length=2000)

    _url = field_validator("stream_url")(_check_stream_url)


class OnvifStreamsIn(BaseModel):
    onvif_url: str = Field(pattern=r"^https?://", max_length=500)
    username: str = Field(max_length=120)
    password: str = Field(max_length=200)


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
    clip_url: str | None = None
    status: str
    note: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    received_at: datetime | None = None  # when the API stored it (minus occurred_at = alert delay)


class EventUpdate(BaseModel):
    status: EventStatus | None = None
    note: str | None = Field(default=None, max_length=2000)
    reviewed_by: str | None = Field(default=None, max_length=120)


class EventPage(BaseModel):
    total: int
    items: list[EventOut]


# --- Rules -------------------------------------------------------------------

RuleType = Literal["zone_entry", "line_crossing", "loitering", "occupancy", "counting_line"]
LINE_TYPES = {"line_crossing", "counting_line"}
DetectClass = Literal["person", "bicycle", "car", "motorcycle", "bus", "truck"]
Point = tuple[float, float]


def _check_points(rule_type: str, points: list[Point]) -> list[Point]:
    for x, y in points:
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("points must be normalized: 0 <= x, y <= 1")
    if rule_type in LINE_TYPES and len(points) != 2:
        raise ValueError("a line needs exactly 2 points")
    if rule_type not in LINE_TYPES and not 3 <= len(points) <= 50:
        raise ValueError("a zone needs 3 to 50 points")
    return points


class Schedule(BaseModel):
    """Active hours in the edge box's local time. start == end means all day; a window that
    crosses midnight (22:00-06:00) belongs to the day it starts on."""

    days: list[int] = Field(min_length=1, max_length=7)  # 0 = Monday
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days must be 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))


class RuleBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    classes: list[DetectClass] = Field(default=["person"], min_length=1)
    severity: Literal["high", "medium", "low"] = "medium"
    cooldown_seconds: float = Field(default=30, ge=0, le=86400)
    direction: Literal["in", "out", "both"] = "both"
    dwell_seconds: float = Field(default=20, ge=1, le=86400)
    max_count: int = Field(default=3, ge=0, le=1000)
    min_confidence: float = Field(default=0.0, ge=0, le=1)  # 0 = use the worker's default
    schedule: Schedule | None = None  # None = always active
    enabled: bool = True


class RuleCreate(RuleBase):
    type: RuleType
    points: list[Point]

    @model_validator(mode="after")
    def _points(self):
        _check_points(self.type, self.points)
        return self


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    points: list[Point] | None = None
    classes: list[DetectClass] | None = Field(default=None, min_length=1)
    severity: Literal["high", "medium", "low"] | None = None
    cooldown_seconds: float | None = Field(default=None, ge=0, le=86400)
    direction: Literal["in", "out", "both"] | None = None
    dwell_seconds: float | None = Field(default=None, ge=1, le=86400)
    max_count: int | None = Field(default=None, ge=0, le=1000)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    schedule: Schedule | None = None  # send null to make the rule always active
    enabled: bool | None = None


class RuleOut(RuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    camera_id: int
    type: RuleType
    points: list[Point]


# --- Analytics & settings ----------------------------------------------------

class CountSampleIn(BaseModel):
    camera_id: int
    bucket_start: datetime
    class_name: str = Field(max_length=60)
    avg_count: float = Field(ge=0)
    max_count: int = Field(ge=0)


class LineCountIn(BaseModel):
    camera_id: int
    rule_id: int | None = None
    rule_name: str = Field(max_length=120)
    bucket_start: datetime
    class_name: str = Field(max_length=60)
    in_count: int = Field(ge=0, le=100_000)
    out_count: int = Field(ge=0, le=100_000)


class LiveObject(BaseModel):
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 normalized 0..1
    cls: str = Field(max_length=30)
    track: int | None = None
    conf: float = Field(ge=0, le=1)


class LiveFrameIn(BaseModel):
    camera_id: int
    at: float  # unix time of the analysed frame
    width: int = Field(ge=1, le=10000)
    height: int = Field(ge=1, le=10000)
    objects: list[LiveObject] = Field(default_factory=list, max_length=300)
    preview: str | None = Field(default=None, max_length=700_000)  # base64 JPEG


class SettingsOut(BaseModel):
    event_retention_days: int
    count_retention_days: int
    clips_enabled: bool
    clip_retention_days: int
    clip_storage_mb: int
    sighting_retention_days: int
    search_enabled: bool
    analysis_fps: float


class SettingsUpdate(BaseModel):
    event_retention_days: int | None = Field(default=None, ge=1, le=3650)
    count_retention_days: int | None = Field(default=None, ge=1, le=3650)
    clips_enabled: bool | None = None
    clip_retention_days: int | None = Field(default=None, ge=1, le=365)
    clip_storage_mb: int | None = Field(default=None, ge=50, le=1_000_000)
    sighting_retention_days: int | None = Field(default=None, ge=1, le=365)
    search_enabled: bool | None = None
    analysis_fps: float | None = Field(default=None, ge=1, le=5)


HTTP_URL = r"^https?://[^\s/$.?#][^\s]*$"


class NotificationSettingsOut(BaseModel):
    enabled: bool
    server: str
    topic: str
    min_severity: Literal["low", "medium", "high"]
    include_snapshot: bool
    dashboard_url: str
    cooldown_seconds: int
    has_token: bool  # the token itself is never returned


class NotificationSettingsUpdate(BaseModel):
    enabled: bool | None = None
    server: str | None = Field(default=None, pattern=HTTP_URL, max_length=300)
    # ntfy topic rules; on a public server the topic name is the only secret, so make it long.
    topic: str | None = Field(default=None, pattern=r"^[-_A-Za-z0-9]{0,64}$")
    min_severity: Literal["low", "medium", "high"] | None = None
    include_snapshot: bool | None = None
    dashboard_url: str | None = Field(default=None, pattern=r"^(https?://[^\s]+)?$", max_length=300)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)
    token: str | None = Field(default=None, max_length=300)  # "" clears it


class ArmingOut(BaseModel):
    armed: bool
    changed_by: str | None = None
    changed_at: datetime | None = None


class ArmingIn(BaseModel):
    armed: bool


class SightingIn(BaseModel):
    camera_id: int
    track_id: int
    object_class: str = Field(max_length=60)
    first_seen: datetime
    last_seen: datetime
    confidence: float = Field(ge=0, le=1)
    path: list[tuple[float, float]] = Field(default=[], max_length=200)
    embedding: str = Field(max_length=20_000)  # base64 float16
    thumbnail: str = Field(max_length=2_000_000)  # base64 JPEG
    context: str = Field(max_length=2_000_000)


class SightingOut(BaseModel):
    id: int
    camera_id: int
    camera_name: str | None
    object_class: str
    first_seen: datetime
    last_seen: datetime
    duration_seconds: float
    confidence: float
    thumbnail_url: str
    context_url: str
    score: float | None = None  # 0..1 relevance for search / similarity results


class SightingDetail(SightingOut):
    path: list[tuple[float, float]]
    clips: list[dict]  # alerts on that camera while it was in view: {event_id, event_type, clip_url, snapshot_url}


class NotificationTestResult(BaseModel):
    ok: bool
    error: str | None = None


# --- Auth & users ------------------------------------------------------------

Role = Literal["admin", "operator"]
USERNAME_PATTERN = r"^[A-Za-z0-9_.@-]{3,60}$"


class LoginIn(BaseModel):
    username: str = Field(max_length=60)
    password: str = Field(max_length=200)


class SetupIn(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=10, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Role
    active: bool
    created_at: datetime
    last_login_at: datetime | None


class AuthStatus(BaseModel):
    setup_required: bool
    user: UserOut | None


class UserCreate(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=10, max_length=200)
    role: Role = "operator"


class UserUpdate(BaseModel):
    role: Role | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    username: str | None
    action: str
    target: str | None
    detail: dict
    ip: str | None


class AuditPage(BaseModel):
    total: int
    items: list[AuditOut]


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
