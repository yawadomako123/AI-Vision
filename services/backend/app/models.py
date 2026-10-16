from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    infer_fps: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

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
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    rule_id: Mapped[str | None] = mapped_column(String(120))
    rule_name: Mapped[str | None] = mapped_column(String(120))
    object_class: Mapped[str | None] = mapped_column(String(60))
    track_id: Mapped[int | None]
    confidence: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    # Operator review workflow.
    status: Mapped[str] = mapped_column(String(20), default="unreviewed", index=True)
    note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    camera: Mapped[Camera] = relationship(back_populates="events")
