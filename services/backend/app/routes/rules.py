from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import current_user, require_admin
from app.db import get_db
from app.live import hub
from app.models import Camera, DetectionRule, User
from app.schemas import RuleCreate, RuleOut, RuleUpdate, _check_points

router = APIRouter(tags=["rules"])


def _notify(camera_id: int) -> None:
    hub.broadcast_from_sync("rules", {"camera_id": camera_id})


@router.get("/api/cameras/{camera_id}/rules", response_model=list[RuleOut])
def list_rules(camera_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(DetectionRule).where(DetectionRule.camera_id == camera_id).order_by(DetectionRule.id)).all()


@router.post("/api/cameras/{camera_id}/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(camera_id: int, body: RuleCreate, request: Request, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")
    rule = DetectionRule(camera_id=camera_id, **body.model_dump())
    db.add(rule)
    audit(db, request, admin, "rule.create", f"camera:{camera.name}", rule=body.name, type=body.type)
    db.commit()
    _notify(camera_id)
    return rule


def _get(db: Session, rule_id: int) -> DetectionRule:
    rule = db.get(DetectionRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return rule


@router.patch("/api/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, body: RuleUpdate, request: Request, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    rule = _get(db, rule_id)
    changes = body.model_dump(exclude_unset=True)
    if "points" in changes:
        try:
            _check_points(rule.type, changes["points"])
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
        changes["points"] = [list(p) for p in changes["points"]]
    for field, value in changes.items():
        setattr(rule, field, value)
    audit(db, request, admin, "rule.update", f"rule:{rule.name}",
          **{k: v for k, v in changes.items() if k != "points"})
    db.commit()
    _notify(rule.camera_id)
    return rule


@router.delete("/api/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rule = _get(db, rule_id)
    camera_id = rule.camera_id
    audit(db, request, admin, "rule.delete", f"rule:{rule.name}")
    db.delete(rule)
    db.commit()
    _notify(camera_id)
