import base64
import time

import numpy as np
import supervision as sv

from app.sightings import SightingCollector


class FakeEmbedder:
    def __call__(self, crop):
        v = np.ones(768, np.float32)
        return v / np.linalg.norm(v)


def dets(boxes, tids, conf=0.9):
    return sv.Detections(xyxy=np.array(boxes, np.float32), confidence=np.full(len(boxes), conf, np.float32),
                         class_id=np.ones(len(boxes), int), tracker_id=np.array(tids))


def run(collector, n, x_step=10, start=0.0, tid=1):
    frame = np.full((480, 640, 3), 80, np.uint8)
    for i in range(n):
        x = 100 + i * x_step
        d = dets([[x, 100, x + 60, 300]], [tid])
        pos = np.array([[x + 30, 300]], np.float32)
        collector.update(d, ["person"], pos, frame, start + i * 0.5)


def wait(sent, n, timeout=10):
    deadline = time.monotonic() + timeout
    while len(sent) < n and time.monotonic() < deadline:
        time.sleep(0.05)


def test_track_becomes_one_sighting_after_it_is_lost():
    sent = []
    c = SightingCollector(3, sent.append, FakeEmbedder(), lost_seconds=4)
    run(c, 10)  # 5 s of a person walking right
    assert sent == []  # still visible
    c.update(dets(np.empty((0, 4)), []), [], np.empty((0, 2)), np.zeros((480, 640, 3), np.uint8), 4.5 + 4.1)
    wait(sent, 1)
    s = sent[0]
    assert s["camera_id"] == 3 and s["track_id"] == 1 and s["object_class"] == "person"
    assert s["first_seen"] < s["last_seen"] and s["confidence"] == 0.9
    assert len(s["path"]) >= 4 and s["path"][-1][0] > s["path"][0][0]  # walked to the right
    emb = np.frombuffer(base64.b64decode(s["embedding"]), np.float16)
    assert emb.shape == (768,) and abs(float(np.linalg.norm(emb.astype(np.float32))) - 1) < 1e-2
    assert base64.b64decode(s["thumbnail"])[:2] == b"\xff\xd8"  # JPEG
    assert base64.b64decode(s["context"])[:2] == b"\xff\xd8"


def test_flicker_is_ignored_and_flush_sends_open_tracks():
    sent = []
    c = SightingCollector(1, sent.append, FakeEmbedder(), min_detections=3)
    run(c, 2, tid=7)  # only 2 detections: a flicker
    run(c, 5, tid=8, start=10)
    c.flush()
    wait(sent, 1)
    time.sleep(0.3)
    assert [s["track_id"] for s in sent] == [8]


def test_disabled_without_embedder():
    sent = []
    c = SightingCollector(1, sent.append, None)
    run(c, 5)
    c.flush()
    assert sent == []


def test_tick_finishes_tracks_when_frames_stop():
    sent = []
    c = SightingCollector(2, sent.append, FakeEmbedder(), lost_seconds=4)
    run(c, 6)  # last update at t=2.5
    c.tick(2.5 + 3)
    time.sleep(0.2)
    assert sent == []  # not long enough
    c.tick(2.5 + 7)
    wait(sent, 1)
    assert len(sent) == 1
