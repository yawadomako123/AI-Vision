import json

import numpy as np
import supervision as sv

from app.rule_engine import Rule, RuleEngine, RulesFile

W = H = 1000
PERSON, CAR = 1, 3
SQUARE = [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]


def dets(*objs) -> sv.Detections:
    """objs: (track_id, foot_x, foot_y[, class_id]) in normalized coords -> 40x100px boxes."""
    if not objs:
        return sv.Detections(
            xyxy=np.empty((0, 4), np.float32), confidence=np.empty(0, np.float32),
            class_id=np.empty(0, int), tracker_id=np.empty(0, int),
        )
    xyxy, cls, tids = [], [], []
    for o in objs:
        tid, x, y, c = (*o, PERSON) if len(o) == 3 else o
        xyxy.append([x * W - 20, y * H - 100, x * W + 20, y * H])
        cls.append(c)
        tids.append(tid)
    n = len(objs)
    return sv.Detections(
        xyxy=np.array(xyxy, np.float32), confidence=np.full(n, 0.9, np.float32),
        class_id=np.array(cls), tracker_id=np.array(tids),
    )


def engine(**rule) -> RuleEngine:
    return RuleEngine([Rule.from_dict({"id": "r1", "points": SQUARE, **rule})])


def test_zone_entry_fires_once_per_entry():
    e = engine(type="zone_entry")
    assert e.update(dets((1, 0.2, 0.5)), W, H, 0.0) == []
    hits = e.update(dets((1, 0.5, 0.5)), W, H, 1.0)
    assert [(h.event_type, h.track_id, h.class_name) for h in hits] == [("zone_entry", 1, "person")]
    assert e.update(dets((1, 0.5, 0.55)), W, H, 2.0) == []  # still inside: no repeat


def test_zone_entry_ignores_other_classes():
    e = engine(type="zone_entry")
    assert e.update(dets((1, 0.5, 0.5, CAR)), W, H, 0.0) == []
    e = engine(type="zone_entry", classes=["car"])
    assert len(e.update(dets((1, 0.5, 0.5, CAR)), W, H, 0.0)) == 1


def local_ts(day_offset: int, hour: int, minute: int = 0) -> float:
    """Local time on Monday 2026-10-12 + day_offset (Mon=0)."""
    from datetime import datetime, timedelta
    base = datetime(2026, 10, 12)
    assert base.weekday() == 0
    return (base + timedelta(days=day_offset, hours=hour, minutes=minute)).timestamp()


def test_schedule_windows():
    from app.rule_engine import schedule_active

    assert schedule_active(None, local_ts(0, 3))
    weekdays_9_17 = {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}
    assert schedule_active(weekdays_9_17, local_ts(0, 9))
    assert not schedule_active(weekdays_9_17, local_ts(0, 17))
    assert not schedule_active(weekdays_9_17, local_ts(5, 12))  # Saturday
    night = {"days": [4], "start": "22:00", "end": "06:00"}  # Friday night
    assert schedule_active(night, local_ts(4, 23))
    assert schedule_active(night, local_ts(5, 5, 59))  # still Friday's window on Saturday morning
    assert not schedule_active(night, local_ts(5, 6))
    assert not schedule_active(night, local_ts(3, 23))  # Thursday night
    assert schedule_active({"days": [6], "start": "00:00", "end": "00:00"}, local_ts(6, 15))  # all day Sunday


def test_inactive_rule_does_not_fire_and_resets():
    sched = {"days": [0], "start": "09:00", "end": "10:00"}
    e = engine(type="zone_entry", schedule=sched)
    assert e.update(dets((1, 0.5, 0.5)), W, H, local_ts(0, 8, 59)) == []  # inside, but not active yet
    # Becomes active while the person is already inside: counts as being in the zone now.
    assert len(e.update(dets((1, 0.5, 0.5)), W, H, local_ts(0, 9, 0))) == 1


def test_rule_min_confidence_filters_weak_detections():
    weak = dets((1, 0.5, 0.5))
    weak.confidence[:] = 0.45
    assert engine(type="zone_entry", min_confidence=0.6).update(weak, W, H, 0.0) == []
    assert len(engine(type="zone_entry", min_confidence=0.4).update(weak, W, H, 0.0)) == 1
    assert len(engine(type="zone_entry").update(weak, W, H, 0.0)) == 1  # unset: worker default applies


def test_zone_entry_uses_feet_not_box_center():
    # Box center is inside the zone (y=0.55) but the feet (y=0.65) are below it.
    e = engine(type="zone_entry")
    assert e.update(dets((1, 0.5, 0.65)), W, H, 0.0) == []


def test_box_cut_off_at_frame_bottom_uses_center():
    # Head-and-shoulders view: box runs off the bottom of the frame, feet not visible.
    # Its feet would be at y=1.0 (outside the zone) but its center (0.5) is inside.
    e = engine(type="zone_entry")
    cut_off = sv.Detections(
        xyxy=np.array([[480, 0.0 * H, 520, 1.0 * H]], np.float32), confidence=np.array([0.9], np.float32),
        class_id=np.array([PERSON]), tracker_id=np.array([1]),
    )
    hits = e.update(cut_off, W, H, 0.0)
    assert len(hits) == 1
    assert hits[0].box == [480.0, 0.0, 520.0, 1000.0]  # event still reports the real box


def test_zone_entry_cooldown_per_track():
    e = engine(type="zone_entry", cooldown_seconds=30)
    assert len(e.update(dets((1, 0.5, 0.5)), W, H, 0.0)) == 1
    e.update(dets((1, 0.2, 0.5)), W, H, 5.0)  # leaves
    assert e.update(dets((1, 0.5, 0.5)), W, H, 10.0) == []  # re-enters within cooldown
    assert len(e.update(dets((2, 0.5, 0.5)), W, H, 11.0)) == 1  # different person still alerts
    e.update(dets(), W, H, 20.0)
    assert len(e.update(dets((1, 0.5, 0.5)), W, H, 40.0)) == 1  # after cooldown


def test_line_crossing_both_directions():
    e = RuleEngine([Rule.from_dict({
        "id": "line", "type": "line_crossing", "points": [[0.5, 0.0], [0.5, 1.0]], "cooldown_seconds": 0,
    })])
    events = []
    for t, x in enumerate([0.3, 0.4, 0.6, 0.7, 0.6, 0.4]):
        events += e.update(dets((7, x, 0.5)), W, H, float(t))
    assert [h.details["direction"] for h in events] == ["in", "out"] or \
           [h.details["direction"] for h in events] == ["out", "in"]


def test_line_crossing_direction_filter():
    def run(direction):
        e = RuleEngine([Rule.from_dict({
            "id": "line", "type": "line_crossing", "points": [[0.5, 0.0], [0.5, 1.0]],
            "direction": direction, "cooldown_seconds": 0,
        })])
        out = []
        for t, x in enumerate([0.3, 0.7, 0.3]):
            out += e.update(dets((7, x, 0.5)), W, H, float(t))
        return [h.details["direction"] for h in out]

    both = run("both")
    assert len(both) == 2
    assert run(both[0]) == [both[0]]


def test_loitering_after_dwell_time():
    e = engine(type="loitering", dwell_seconds=10)
    assert e.update(dets((1, 0.5, 0.5)), W, H, 0.0) == []
    assert e.update(dets((1, 0.5, 0.5)), W, H, 9.0) == []
    hits = e.update(dets((1, 0.5, 0.5)), W, H, 10.5)
    assert len(hits) == 1 and hits[0].details["dwell_seconds"] == 10.5
    assert e.update(dets((1, 0.5, 0.5)), W, H, 30.0) == []  # reported once per stay


def test_loitering_timer_resets_when_leaving():
    e = engine(type="loitering", dwell_seconds=10, cooldown_seconds=0)
    e.update(dets((1, 0.5, 0.5)), W, H, 0.0)
    e.update(dets((1, 0.2, 0.5)), W, H, 6.0)  # stepped out
    assert e.update(dets((1, 0.5, 0.5)), W, H, 12.0) == []  # only 0s into the new stay
    assert len(e.update(dets((1, 0.5, 0.5)), W, H, 22.5)) == 1


def test_occupancy_fires_once_and_rearms():
    e = engine(type="occupancy", max_count=2, cooldown_seconds=0)
    three = dets((1, 0.45, 0.5), (2, 0.5, 0.5), (3, 0.55, 0.5))
    hits = e.update(three, W, H, 0.0)
    assert len(hits) == 1 and hits[0].details["count"] == 3
    assert sorted(hits[0].details["track_ids"]) == [1, 2, 3]
    assert e.update(three, W, H, 1.0) == []  # still over: no repeat
    e.update(dets((1, 0.45, 0.5)), W, H, 2.0)  # back under
    assert len(e.update(three, W, H, 3.0)) == 1


def test_rules_file_reload_and_invalid_json(tmp_path):
    path = tmp_path / "rules.json"
    rf = RulesFile(path)
    assert rf.changed() is False and rf.for_camera("cam1") == []

    path.write_text(json.dumps({"cam1": [{"id": "z", "type": "zone_entry", "points": SQUARE}]}))
    assert rf.changed() is True
    assert [r.id for r in rf.for_camera("cam1")] == ["z"]

    path.write_text("{not json")
    import os
    os.utime(path, (1, 1))  # force a different mtime
    assert rf.changed() is False  # invalid file keeps previous rules
    assert [r.id for r in rf.for_camera("cam1")] == ["z"]


def test_rule_validation():
    import pytest

    with pytest.raises(ValueError):
        Rule.from_dict({"id": "x", "type": "teleport", "points": SQUARE})
    with pytest.raises(ValueError):
        Rule.from_dict({"id": "x", "type": "zone_entry", "points": [[0, 0], [1, 1]]})
    with pytest.raises(ValueError):
        Rule.from_dict({"id": "x", "type": "line_crossing", "points": SQUARE})
