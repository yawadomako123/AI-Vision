"""Persists events: annotated JPEG snapshot + one JSON line per event.

Output layout (EVENT_STORAGE_PATH):
    events.jsonl
    2026/10/16/20261016T101500_cam1_zone_entry_a1b2c3.jpg

When the FastAPI backend exists this module will POST the same payload to it instead.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from app.rule_engine import RuleHit
from app.overlay import draw_hit, draw_rules

log = logging.getLogger(__name__)

SYSTEM_SEVERITY = {
    "camera_tamper": "high",
    "camera_tamper_cleared": "low","camera_offline": "high", "camera_online": "low"}


class EventPublisher:
    def __init__(self, storage_path: Path):
        self.root = storage_path
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "events.jsonl"

    def publish(self, camera_id: str, hit: RuleHit, frame: np.ndarray | None, rules=(), clips=None) -> dict:
        ts = datetime.fromtimestamp(hit.occurred_at, tz=timezone.utc)
        event_id = f"{ts:%Y%m%dT%H%M%S}_{camera_id}_{hit.event_type}_{uuid.uuid4().hex[:6]}"

        snapshot = None
        if frame is not None:
            rel = Path(f"{ts:%Y/%m/%d}") / f"{event_id}.jpg"
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            img = draw_rules(frame.copy(), rules, highlight=hit.rule.id if hit.rule else None)
            img = draw_hit(img, hit)
            cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            snapshot = rel.as_posix()

        clip = None
        if clips is not None and hit.rule is not None:
            started = clips.start(self.root / f"{ts:%Y/%m/%d}" / f"{event_id}.mp4", hit.occurred_at)
            clip = started.relative_to(self.root).as_posix() if started else None

        event = {
            "id": event_id,
            "camera_id": camera_id,
            "event_type": hit.event_type,
            "occurred_at": ts.isoformat().replace("+00:00", "Z"),
            "severity": hit.rule.severity if hit.rule else SYSTEM_SEVERITY.get(hit.event_type, "medium"),
            "rule": {"id": hit.rule.id, "name": hit.rule.name, "type": hit.rule.type} if hit.rule else None,
            "object": (
                {"class": hit.class_name, "track_id": hit.track_id, "confidence": hit.confidence, "box": hit.box}
                if hit.class_name else None
            ),
            "details": hit.details,
            "evidence": ({"snapshot_path": snapshot, **({"clip_path": clip} if clip else {})}
                         if snapshot or clip else None),
            "status": "unreviewed",
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        log.info("EVENT %s %s %s", camera_id, hit.event_type, hit.rule.name if hit.rule else "")
        return event
