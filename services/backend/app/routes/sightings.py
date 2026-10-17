import base64
import binascii
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.audit import audit
from app.auth import current_user
from app.config import settings
from app.db import get_db
from app.models import Camera, Event, Sighting, User
from app.routes.internal import require_service_token
from app.schemas import SightingDetail, SightingIn, SightingOut
from app.search_engine import (
    SearchUnavailable, TextEncoder, decode_embedding, image_relevance, rank, text_relevance,
)

router = APIRouter(tags=["search"])
text_encoder = TextEncoder(settings.search_model_dir)
MEDIA_PREFIX = "/media/events/"


def _out(s: Sighting, score: float | None = None) -> SightingOut:
    return SightingOut(
        id=s.id, camera_id=s.camera_id, camera_name=s.camera.name if s.camera else None,
        object_class=s.object_class, first_seen=s.first_seen, last_seen=s.last_seen,
        duration_seconds=round((s.last_seen - s.first_seen).total_seconds(), 1), confidence=s.confidence,
        thumbnail_url=MEDIA_PREFIX + s.thumbnail_path, context_url=MEDIA_PREFIX + s.context_path, score=score,
    )


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
    db.add(s)
    db.commit()
    return {"id": s.id}


def _filters(camera_id: int | None, object_class: str | None, hours: int) -> list:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    f = [Sighting.first_seen >= since]
    if camera_id:
        f.append(Sighting.camera_id == camera_id)
    if object_class == "vehicle":
        f.append(Sighting.object_class.in_(["car", "truck", "bus", "motorcycle", "bicycle"]))
    elif object_class:
        f.append(Sighting.object_class == object_class)
    return f


@router.get("/api/search", response_model=list[SightingOut])
def search(
    request: Request,
    q: str = Query(default="", max_length=200),
    camera_id: int | None = None,
    object_class: str | None = Query(default=None, max_length=30),
    hours: int = Query(default=24 * 7, ge=1, le=24 * 365),
    limit: int = Query(default=24, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Plain-English search ("person in a white hoodie"). With no query: most recent sightings."""
    filters = _filters(camera_id, object_class, hours)
    if not q.strip():
        rows = db.scalars(select(Sighting).options(joinedload(Sighting.camera)).where(*filters)
                          .order_by(Sighting.first_seen.desc()).limit(limit)).all()
        return [_out(s) for s in rows]
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
    return [_out(by_id[i], round(text_relevance(c), 3)) for c, i in top if i in by_id and text_relevance(c) > 0]


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
def search_status(_: User = Depends(current_user)):
    return {"available": text_encoder.available}

