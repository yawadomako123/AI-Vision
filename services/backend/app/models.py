from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """A timezone-aware UTC datetime on every database.

    PostgreSQL keeps the timezone; SQLite stores plain text and hands back naive datetimes, which
    break comparisons with aware ones and make .timestamp() use local time. Values are stored as
    UTC and always returned with tzinfo=UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
            if dialect.name == "sqlite":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    location: Mapped[str | None] = mapped_column(String(200))
    stream_url_encrypted: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Reported by the vision worker.
    status: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    infer_fps: Mapped[float | None] = mapped_column(Float)
    # Phone notifications from this camera are silenced until then (detection keeps running).
    notify_muted_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Hardware address: finds the camera again when the router gives it a new IP (app/camera_finder.py).
    mac: Mapped[str | None] = mapped_column(String(17), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)

    events: Mapped[list["Event"]] = relationship(back_populates="camera", passive_deletes=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_camera_time", "camera_id", "occurred_at"),
        Index("ix_events_type_time", "event_type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Worker-generated id; makes event submission idempotent across retries.
    external_id: Mapped[str] = mapped_column(String(120), unique=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    rule_id: Mapped[str | None] = mapped_column(String(120))
    rule_name: Mapped[str | None] = mapped_column(String(120))
    object_class: Mapped[str | None] = mapped_column(String(60))
    track_id: Mapped[int | None]
    confidence: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    clip_path: Mapped[str | None] = mapped_column(Text)
    # Operator review workflow.
    status: Mapped[str] = mapped_column(String(20), default="unreviewed", index=True)
    note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    camera: Mapped[Camera] = relationship(back_populates="events")


class DetectionRule(Base):
    """Zone / line rule for one camera. Geometry is normalized (0..1) so it survives resolution changes."""

    __tablename__ = "detection_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(30))  # zone_entry | line_crossing | loitering | occupancy | privacy_mask
    name: Mapped[str] = mapped_column(String(120))
    points: Mapped[list] = mapped_column(JSON)
    classes: Mapped[list] = mapped_column(JSON, default=lambda: ["person"])
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    cooldown_seconds: Mapped[float] = mapped_column(Float, default=30)
    direction: Mapped[str] = mapped_column(String(10), default="both")
    # Lines always count crossings (entry/exit analytics); alert=False makes one count-only.
    alert: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    dwell_seconds: Mapped[float] = mapped_column(Float, default=20)
    max_count: Mapped[int] = mapped_column(default=3)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    schedule: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # None = always active
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class Sighting(Base):
    """One tracked person/vehicle on one camera: when, where it walked, a cut-out, and a SigLIP
    embedding (the "meaning fingerprint" used for plain-English search). ~25 KB, no video."""

    __tablename__ = "sightings"
    __table_args__ = (Index("ix_sightings_camera_time", "camera_id", "first_seen"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    track_id: Mapped[int]
    object_class: Mapped[str] = mapped_column(String(60), index=True)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime())
    confidence: Mapped[float] = mapped_column(Float)
    path: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # float16, L2-normalised
    # Vehicles only (app/vehicle_attrs.py); None = not a vehicle, or unsure (e.g. lights at night).
    color: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    vehicle_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # Number plate (letters/digits only), its reading confidence and a picture of the plate.
    plate: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    plate_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    plate_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str] = mapped_column(Text)
    context_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    camera: Mapped[Camera] = relationship()


class CountSample(Base):
    """Per-minute object counts reported by the worker (people/vehicle analytics)."""

    __tablename__ = "count_samples"
    __table_args__ = (Index("ix_counts_camera_bucket", "camera_id", "bucket_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    bucket_start: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    class_name: Mapped[str] = mapped_column(String(60))
    avg_count: Mapped[float] = mapped_column(Float)
    max_count: Mapped[int]


class LineCount(Base):
    """Per-minute entry/exit totals from one counting line. Only minutes with crossings are stored."""

    __tablename__ = "line_counts"
    __table_args__ = (Index("ix_line_counts_camera_bucket", "camera_id", "bucket_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    # Kept (with its name) after the rule is deleted, so past counts stay in the reports.
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("detection_rules.id", ondelete="SET NULL"), nullable=True)
    rule_name: Mapped[str] = mapped_column(String(120))
    bucket_start: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    class_name: Mapped[str] = mapped_column(String(60))  # person | vehicle
    in_count: Mapped[int]
    out_count: Mapped[int]


class WatchedPlate(Base):
    """A plate to alert on (a stolen or banned car) or simply to name (a staff car)."""

    __tablename__ = "watched_plates"

    id: Mapped[int] = mapped_column(primary_key=True)
    plate: Mapped[str] = mapped_column(String(16), unique=True)  # normalized
    label: Mapped[str] = mapped_column(String(120))
    alert: Mapped[bool] = mapped_column(Boolean, default=True)  # False = just put a name to it
    created_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="operator")  # admin | operator
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class UserSession(Base):
    """Server-side sessions, so logout and deactivating a user take effect immediately."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the cookie value
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())

    user: Mapped[User] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    username: Mapped[str | None] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60), index=True)
    target: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
