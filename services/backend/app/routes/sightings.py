import base64
import binascii
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from PIL import Image
from sqlalchemy.orm import Session, joinedload

from app.audit import audit
from app.auth import current_user
from app.config import settings
from app.db import get_db
from app.models import Camera, Event, Sighting, User, WatchedPlate
from app.plates import SEARCH_MATCH, WATCH_MATCH, similarity
from app.plates import normalize as normalize_plate
from app.routes.internal import publish_event, require_service_token
from app.schemas import SightingDetail, SightingIn, SightingOut
from app.search_engine import (
    SearchUnavailable, TextEncoder, decode_embedding, image_relevance, rank, text_relevance,
)
from app.settings_store import get_settings
from app.vehicle_attrs import VEHICLE_CLASSES, VehicleAttributes

router = APIRouter(tags=["search"])
text_encoder = TextEncoder(settings.search_model_dir)
vehicle_attrs = VehicleAttributes(text_encoder)
log = logging.getLogger(__name__)
MEDIA_PREFIX = "/media/events/"


def _out(s: Sighting, score: float | None = None, labels: dict[str, str] | None = None) -> SightingOut:
    return SightingOut(
        id=s.id, camera_id=s.camera_id, camera_name=s.camera.name if s.camera else None,
        object_class=s.object_class, first_seen=s.first_seen, last_seen=s.last_seen,
        duration_seconds=round((s.last_seen - s.first_seen).total_seconds(), 1), confidence=s.confidence,
        thumbnail_url=MEDIA_PREFIX + s.thumbnail_path, context_url=MEDIA_PREFIX + s.context_path, score=score,
        color=s.color, vehicle_type=s.vehicle_type,
        plate=s.plate, plate_confidence=s.plate_confidence,
        plate_url=MEDIA_PREFIX + s.plate_path if s.plate_path else None,
        plate_label=labels.get(s.plate) if labels and s.plate else None,
    )


def _labels(db: Session) -> dict[str, str]:
    return {w.plate: w.label for w in db.scalars(select(WatchedPlate))}


def check_watchlist(db: Session, s: Sighting) -> None:
    """A watched plate was seen: raise a high-severity alert (with the sighting's picture)."""
    if not s.plate:
        return
    best = max(((similarity(s.plate, w.plate), w) for w in db.scalars(select(WatchedPlate).where(WatchedPlate.alert))),
               key=lambda x: x[0], default=(0.0, None))
    score, w = best
    if w is None or score < WATCH_MATCH:
        return
    publish_event(db, Event(
        external_id=f"plate-{s.id}", camera_id=s.camera_id, event_type="plate_watchlist", severity="high",
        occurred_at=s.first_seen, rule_name=w.label, object_class=s.object_class, confidence=s.plate_confidence,
        details={"plate": s.plate, "watched_plate": w.plate, "label": w.label, "match": round(score, 2),
                 "sighting_id": s.id},
        snapshot_path=s.context_path,
    ))


def describe_vehicle(s: Sighting, thumbnail_jpeg: bytes | None) -> None:
    """Fill in colour and body type for a vehicle sighting. Never fails the caller."""
    if s.object_class not in VEHICLE_CLASSES or not vehicle_attrs.available:
        return
    rgb = None
    try:  # pixels for the poor-light check; without them colour/type still work
        rgb = np.asarray(Image.open(io.BytesIO(thumbnail_jpeg)).convert("RGB")) if thumbnail_jpeg else None
    except Exception:  # noqa: BLE001
        pass
    try:
        r = vehicle_attrs.classify(decode_embedding(s.embedding), s.object_class, rgb)
        s.color, s.vehicle_type = r.get("color"), r.get("vehicle_type")
    except Exception:  # noqa: BLE001 - a description is a bonus; the sighting itself matters
        log.exception("vehicle description failed for a sighting")


def _save_jpeg(b64: str, rel: str) -> None:
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid image")
    if data[:2] != b"\xff\xd8":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "images must be JPEG")
    path = settings.event_storage_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@router.post("/api/internal/sightings", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_service_token)], include_in_schema=False)
def ingest_sighting(body: SightingIn, db: Session = Depends(get_db)):
    if db.get(Camera, body.camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")
    try:
        emb = base64.b64decode(body.embedding, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid embedding")
    if len(emb) % 2 or not 64 <= len(emb) // 2 <= 4096:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "embedding must be float16, 64-4096 dims")
    key = f"sightings/{body.first_seen:%Y/%m/%d}/{uuid.uuid4().hex[:12]}"
    _save_jpeg(body.thumbnail, key + "_t.jpg")
    _save_jpeg(body.context, key + "_c.jpg")
    s = Sighting(camera_id=body.camera_id, track_id=body.track_id, object_class=body.object_class,
                 first_seen=body.first_seen, last_seen=body.last_seen, confidence=body.confidence,
                 path=[list(p) for p in body.path], embedding=emb,
                 thumbnail_path=key + "_t.jpg", context_path=key + "_c.jpg")
    describe_vehicle(s, base64.b64decode(body.thumbnail))
    plate = normalize_plate(body.plate)
    if plate and get_settings(db)["plates_enabled"]:
        s.plate, s.plate_confidence = plate, body.plate_confidence
        if body.plate_image:
            _save_jpeg(body.plate_image, key + "_p.jpg")
            s.plate_path = key + "_p.jpg"
    db.add(s)
    db.commit()
    check_watchlist(db, s)
    return {"id": s.id}


def _filters(camera_id: int | None, object_class: str | None, hours: int,
             color: str | None = None, vehicle_type: str | None = None) -> list:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    f = [Sighting.first_seen >= since]
    if camera_id:
        f.append(Sighting.camera_id == camera_id)
    if object_class == "vehicle":
        f.append(Sighting.object_class.in_(["car", "truck", "bus", "motorcycle", "bicycle"]))
    elif object_class:
        f.append(Sighting.object_class == object_class)
    if color:
        f.append(Sighting.color == color)
    if vehicle_type:
        f.append(Sighting.vehicle_type == vehicle_type)
    return f


@router.get("/api/search", response_model=list[SightingOut])
def search(
    request: Request,
    q: str = Query(default="", max_length=200),
    camera_id: int | None = None,
    object_class: str | None = Query(default=None, max_length=30),
    hours: int = Query(default=24 * 7, ge=1, le=24 * 365),
    limit: int = Query(default=24, ge=1, le=100),
    color: str | None = Query(default=None, max_length=20),
    vehicle_type: str | None = Query(default=None, max_length=20),
    plate: str | None = Query(default=None, max_length=20),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Plain-English search ("person in a white hoodie"). With no query: most recent sightings.
    color / vehicle_type narrow vehicle results (e.g. red pickups)."""
    if not get_settings(db)["search_enabled"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Search is switched off. An admin can turn it on in Settings.")
    filters = _filters(camera_id, object_class, hours, color, vehicle_type)
    labels = _labels(db)
    if plate and normalize_plate(plate):
        # Closest plates first; tolerant of a misread character or two.
        rows = db.scalars(select(Sighting).options(joinedload(Sighting.camera))
                          .where(*filters, Sighting.plate.is_not(None))).all()
        scored = sorted(((similarity(plate, s.plate), s) for s in rows), key=lambda x: (-x[0], -x[1].first_seen.timestamp()))
        audit(db, request, user, "search", detail_plate=normalize_plate(plate))
        db.commit()
        return [_out(s, round(sc, 3), labels) for sc, s in scored if sc >= SEARCH_MATCH][:limit]
    if not q.strip():
        rows = db.scalars(select(Sighting).options(joinedload(Sighting.camera)).where(*filters)
                          .order_by(Sighting.first_seen.desc()).limit(limit)).all()
        return [_out(s, None, labels) for s in rows]
    try:
        query = text_encoder.encode(q.strip())
    except SearchUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    candidates = db.execute(select(Sighting.id, Sighting.embedding).where(*filters)).all()
    scores = rank(query, [e for _, e in candidates])
    top = sorted(zip(scores.tolist(), [i for i, _ in candidates]), reverse=True)[:limit]
    by_id = {s.id: s for s in db.scalars(select(Sighting).options(joinedload(Sighting.camera))
                                         .where(Sighting.id.in_([i for _, i in top])))}
    audit(db, request, user, "search", detail_q=q[:100], results=len(top))
    db.commit()
    return [_out(by_id[i], round(text_relevance(c), 3), labels) for c, i in top if i in by_id and text_relevance(c) > 0]


def _get(db: Session, sighting_id: int) -> Sighting:
    s = db.get(Sighting, sighting_id, options=[joinedload(Sighting.camera)])
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sighting not found")
    return s


@router.get("/api/sightings/{sighting_id}", response_model=SightingDetail)
def sighting_detail(sighting_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _get(db, sighting_id)
    # Alerts on this camera while it was in view (with a little slack): their clips show what happened.
    events = db.scalars(select(Event).where(
        Event.camera_id == s.camera_id,
        Event.occurred_at >= s.first_seen - timedelta(seconds=10),
        Event.occurred_at <= s.last_seen + timedelta(seconds=10),
    ).order_by(Event.occurred_at)).all()
    clips = [{"event_id": e.id, "event_type": e.event_type, "occurred_at": e.occurred_at,
              "clip_url": MEDIA_PREFIX + e.clip_path if e.clip_path else None,
              "snapshot_url": MEDIA_PREFIX + e.snapshot_path if e.snapshot_path else None} for e in events]
    return SightingDetail(**_out(s).model_dump(), path=[tuple(p) for p in s.path], clips=clips)


@router.get("/api/sightings/{sighting_id}/similar", response_model=list[SightingOut])
def similar(sighting_id: int, hours: int = Query(default=24 * 7, ge=1, le=24 * 365),
            min_score: float = Query(default=0.5, ge=0, le=1), limit: int = Query(default=50, ge=1, le=200),
            _: User = Depends(current_user), db: Session = Depends(get_db)):
    """Sightings that look like this one (any camera), in time order: a 'where was this seen' timeline.
    Appearance only (clothing, shape, colour), not identity."""
    s = _get(db, sighting_id)
    query = decode_embedding(s.embedding)
    query /= np.linalg.norm(query) + 1e-12
    candidates = db.execute(select(Sighting.id, Sighting.embedding).where(
        *_filters(None, None, hours), Sighting.object_class == s.object_class)).all()
    scores = rank(query, [e for _, e in candidates])
    keep = [(image_relevance(c), i) for c, i in zip(scores.tolist(), [i for i, _ in candidates])
            if image_relevance(c) >= min_score or i == s.id]
    keep = sorted(keep, reverse=True)[:limit]
    by_id = {x.id: x for x in db.scalars(select(Sighting).options(joinedload(Sighting.camera))
                                         .where(Sighting.id.in_([i for _, i in keep])))}
    timeline = [_out(by_id[i], round(sc, 3)) for sc, i in keep if i in by_id]
    return sorted(timeline, key=lambda o: o.first_seen)


@router.get("/api/search/status")
def search_status(_: User = Depends(current_user), db: Session = Depends(get_db)):
    """available = model installed; enabled = switched on in Settings."""
    return {"available": text_encoder.available, "enabled": get_settings(db)["search_enabled"]}

