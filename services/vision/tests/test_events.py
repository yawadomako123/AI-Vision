import json

import cv2
import numpy as np

from app.event_publisher import EventPublisher
from app.rule_engine import Rule, RuleHit


def test_publish_writes_snapshot_and_jsonl(tmp_path):
    rule = Rule.from_dict({"id": "door", "type": "zone_entry", "name": "Door",
                           "points": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]})
    hit = RuleHit(rule=rule, event_type="zone_entry", occurred_at=1_760_000_000.0, track_id=4,
                  class_name="person", confidence=0.91, box=[10, 10, 60, 120])
    event = EventPublisher(tmp_path).publish("cam1", hit, np.zeros((240, 320, 3), np.uint8), [rule])

    assert event["severity"] == "high"
    assert event["object"] == {"class": "person", "track_id": 4, "confidence": 0.91, "box": [10, 10, 60, 120]}
    snapshot = tmp_path / event["evidence"]["snapshot_path"]
    assert cv2.imread(str(snapshot)).shape == (240, 320, 3)
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["id"] == event["id"]


def test_system_event_without_frame(tmp_path):
    hit = RuleHit(rule=None, event_type="camera_offline", occurred_at=1_760_000_000.0,
                  details={"offline_seconds": 16.0})
    event = EventPublisher(tmp_path).publish("cam2", hit, None)
    assert event["severity"] == "high"
    assert event["rule"] is None and event["object"] is None and event["evidence"] is None
