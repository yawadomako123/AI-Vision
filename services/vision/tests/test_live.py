import base64

import numpy as np
import supervision as sv

from app.live import LivePublisher, live_payload


def dets():
    return sv.Detections(xyxy=np.array([[64, 48, 128, 480]], np.float32), confidence=np.array([0.876], np.float32),
                         class_id=np.array([1]), tracker_id=np.array([5]))


def test_payload_normalizes_boxes_and_shrinks_preview():
    img = np.zeros((960, 1280, 3), np.uint8)
    p = live_payload(3, dets(), ["person"], 1280, 960, 100.0, img)
    assert p["objects"] == [{"box": [0.05, 0.05, 0.1, 0.5], "cls": "person", "track": 5, "conf": 0.876}]
    jpeg = base64.b64decode(p["preview"])
    assert jpeg[:2] == b"\xff\xd8" and len(jpeg) < 50_000
    assert live_payload(3, dets(), ["person"], 1280, 960, 100.0)["preview"] is None


def test_publisher_keeps_only_newest_and_unsent_preview():
    pub = LivePublisher("http://127.0.0.1:9", "t")  # nothing listens: sends fail quietly
    try:
        assert pub.wants_preview(1, 10.0) and not pub.wants_preview(1, 10.5) and pub.wants_preview(1, 11.1)
        pub._stop.set()  # hold the sender so we can inspect the slot
        pub.offer({"camera_id": 1, "at": 1, "preview": "AAA"})
        pub.offer({"camera_id": 1, "at": 2, "preview": None})
        assert pub._pending[1] == {"camera_id": 1, "at": 2, "preview": "AAA"}
    finally:
        pub.close()
