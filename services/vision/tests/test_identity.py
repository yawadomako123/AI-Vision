"""Stable numbers and recognising returning people (app/identity.py), and the rule engine using them."""

import numpy as np
import supervision as sv

from app.identity import Identities
from app.rule_engine import Rule, RuleEngine

RED, BLUE, GREEN = (0, 0, 220), (220, 0, 0), (0, 200, 0)


def fake_reid(crop: np.ndarray) -> np.ndarray:
    """An "appearance fingerprint" from clothing colour: enough to tell the test people apart."""
    v = crop.reshape(-1, 3).mean(axis=0).astype(np.float32) + 1.0
    return v / np.linalg.norm(v)


def scene(people: list[tuple[int, tuple[int, int, int], int]]) -> tuple[np.ndarray, sv.Detections]:
    """people: (raw tracker id, colour, x position). Returns the picture and tracked detections."""
    image = np.full((480, 640, 3), 128, np.uint8)
    boxes = []
    for _, colour, x in people:
        image[100:300, x:x + 60] = colour
        boxes.append([x, 100, x + 60, 300])
    dets = sv.Detections(xyxy=np.array(boxes, float).reshape(-1, 4), confidence=np.full(len(people), 0.9),
                         class_id=np.zeros(len(people), int), tracker_id=np.array([p[0] for p in people], int))
    return image, dets


def assign(ids: Identities, people, now: float) -> list[int]:
    image, dets = scene(people)
    out = ids.assign(dets, ["person"] * len(people), image, now)
    return [int(t) for t in out.tracker_id]


def test_numbers_stay_small_instead_of_raw_tracker_ids():
    ids = Identities(reid=None)
    assert assign(ids, [(21, RED, 50), (25, BLUE, 300)], 0.0) == [1, 2]
    assert assign(ids, [(21, RED, 60), (25, BLUE, 310)], 0.3) == [1, 2]
    assert assign(ids, [(31, GREEN, 500)], 0.6) == [3]  # without re-id a new track is a new number


def test_a_person_who_leaves_and_comes_back_keeps_their_number():
    ids = Identities(reid=fake_reid)
    assert assign(ids, [(1, RED, 50), (2, BLUE, 300)], 0.0) == [1, 2]
    assign(ids, [(2, BLUE, 320)], 5.0)                     # red walked out of the picture
    assert assign(ids, [(2, BLUE, 340), (7, RED, 500)], 30.0) == [2, 1]  # red is back (new track)
    assert ids.recognised == 1


def test_someone_new_gets_a_new_number_and_visible_people_are_never_reused():
    ids = Identities(reid=fake_reid)
    assign(ids, [(1, RED, 50)], 0.0)
    # Another person in red while #1 is still in the picture: can't be #1.
    assert assign(ids, [(1, RED, 50), (2, RED, 400)], 1.0) == [1, 2]
    # Someone in green after red left: doesn't look like anyone known.
    assert assign(ids, [(3, GREEN, 200)], 10.0) == [3]


def test_two_lookalikes_who_left_stay_apart_rather_than_merge():
    ids = Identities(reid=fake_reid)
    assign(ids, [(1, RED, 50), (2, RED, 400)], 0.0)        # two people dressed alike
    assert assign(ids, [(5, RED, 200)], 20.0) == [3]       # no clear winner: a new number, not a guess


def test_people_are_forgotten_after_a_while():
    ids = Identities(reid=fake_reid, remember_seconds=60)
    assign(ids, [(1, RED, 50)], 0.0)
    assert assign(ids, [(9, RED, 50)], 100.0) == [2]


def test_raw_track_kept_and_tracker_reset():
    ids = Identities(reid=None)
    image, dets = scene([(40, RED, 50)])
    out = ids.assign(dets, ["person"], image, 0.0)
    assert list(out.data["raw_track"]) == [40] and list(out.tracker_id) == [1]
    ids.tracker_reset()  # a new tracker numbers from 1 again: raw 1 must not inherit anything
    assert assign(ids, [(1, BLUE, 300)], 1.0) == [2]


def _with_raw(d: sv.Detections, raw: list[int]) -> sv.Detections:
    d.data["raw_track"] = np.array(raw, int)
    return d


def test_a_returning_person_does_not_cross_a_line_between_leaving_and_coming_back():
    from test_rules import H, W, dets
    line = {"type": "line_crossing", "points": [[0.5, 0.0], [0.5, 1.0]], "alert": True, "direction": "both",
            "cooldown_seconds": 0}
    # Control: walking across the line counts.
    e = RuleEngine([Rule.from_dict({"id": "door", **line})])
    for t, x in ((0.0, 0.3), (0.3, 0.4), (0.6, 0.6), (0.9, 0.7)):
        e.update(_with_raw(dets((1, x, 0.5)), [1]), W, H, t)
    assert len(e.take_crossings()) == 1
    # Left on the left, recognised coming back on the right (same number, new raw track): no crossing.
    e = RuleEngine([Rule.from_dict({"id": "door", **line})])
    for t, x, raw in ((0.0, 0.3, 1), (0.3, 0.35, 1), (30.0, 0.7, 8), (30.3, 0.75, 8), (30.6, 0.8, 8)):
        e.update(_with_raw(dets((1, x, 0.5)), [raw]), W, H, t)
    assert e.take_crossings() == []


def test_loitering_timer_survives_a_brief_miss():
    from test_rules import H, SQUARE, W, dets
    e = RuleEngine([Rule.from_dict({"id": "porch", "type": "loitering", "points": SQUARE, "dwell_seconds": 10,
                                    "cooldown_seconds": 0})])
    e.update(dets((4, 0.5, 0.5)), W, H, 0.0)
    e.update(dets(), W, H, 4.0)                       # missed for a moment (or someone walked in front)
    hits = e.update(dets((4, 0.5, 0.5)), W, H, 10.5)
    assert [h.event_type for h in hits] == ["loitering"]
