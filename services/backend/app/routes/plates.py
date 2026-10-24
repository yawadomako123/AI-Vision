"""Watched plates: a list of plates to alert on (or just to name), managed by admins."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import current_user, require_admin
from app.db import get_db
from app.models import Sighting, User, WatchedPlate
from app.plates import normalize
from app.schemas import WatchedPlateIn, WatchedPlateOut

router = APIRouter(prefix="/api/plates", tags=["plates"])


@router.get("/watchlist", response_model=list[WatchedPlateOut])
def list_watchlist(_: User = Depends(current_user), db: Session = Depends(get_db)):
    last = dict(db.execute(select(Sighting.plate, func.max(Sighting.first_seen))
                           .where(Sighting.plate.is_not(None)).group_by(Sighting.plate)).all())
    out = []
    for w in db.scalars(select(WatchedPlate).order_by(WatchedPlate.label)):
        o = WatchedPlateOut.model_validate(w)
        o.last_seen = last.get(w.plate)
        out.append(o)
    return out


@router.post("/watchlist", response_model=WatchedPlateOut, status_code=status.HTTP_201_CREATED)
def add_watched(body: WatchedPlateIn, request: Request, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    plate = normalize(body.plate)
    if len(plate) < 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "a plate needs letters or digits")
    w = WatchedPlate(plate=plate, label=body.label.strip(), alert=body.alert, created_by=admin.username)
    db.add(w)
    audit(db, request, admin, "plate.watch", f"plate:{plate}", label=w.label, alert=w.alert)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "that plate is already on the list") from None
    return WatchedPlateOut.model_validate(w)


@router.delete("/watchlist/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_watched(watch_id: int, request: Request, admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    w = db.get(WatchedPlate, watch_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not on the list")
    audit(db, request, admin, "plate.unwatch", f"plate:{w.plate}")
    db.delete(w)
    db.commit()
