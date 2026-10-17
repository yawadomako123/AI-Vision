"""Turns tracked detections into rule hits: zone entry, line crossing, loitering, occupancy.
Counting lines raise no alerts: their crossings are collected for entry/exit analytics.

Geometry is stored normalized (0..1) so rules survive resolution changes; it is scaled to
pixels on the first frame of each size. Objects are located by their bottom-center point
(where a person's feet touch the ground), which is what a zone drawn on the floor means;
see object_positions for the cut-off-at-frame-edge fallback.
"""

import logging
from datetime import datetime
from dataclasses import dataclass, field

import numpy as np
import supervision as sv

from app.detector import COCO_CLASSES

log = logging.getLogger(__name__)

RULE_TYPES = {"zone_entry", "line_crossing", "loitering", "occupancy", "counting_line"}
LINE_TYPES = {"line_crossing", "counting_line"}
ANCHOR = (sv.Position.BOTTOM_CENTER,)
DEFAULT_SEVERITY = {"zone_entry": "high", "line_crossing": "medium", "loitering": "medium", "occupancy": "medium", "counting_line": "low"}


@dataclass
class Rule:
    id: str
    type: str
    name: str
    points: list[list[float]]  # polygon (>= 3 points) or line (exactly 2), normalized
    classes: set[str] = field(default_factory=lambda: {"person"})
    severity: str = "medium"
    cooldown_seconds: float = 30.0
    # line_crossing: in | out | both. "in" = crossing left to right, facing from point 1 to point 2.
    direction: str = "both"
    dwell_seconds: float = 20.0  # loitering
    max_count: int = 3  # occupancy: alert when count > max_count
    min_confidence: float = 0.0  # ignore detections below this for this rule (0 = worker default)
    # None = always active, else {"days": [0..6] (Mon=0), "start": "HH:MM", "end": "HH:MM"}
    schedule: dict | None = None
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        rule_type = d["type"]
        if rule_type not in RULE_TYPES:
            raise ValueError(f"unknown rule type {rule_type!r}")
        points = d.get("points") or d.get("polygon") or [d["start"], d["end"]]
        need = 2 if rule_type in LINE_TYPES else 3
        if len(points) < need or (rule_type in LINE_TYPES and len(points) != 2):
            raise ValueError(f"rule {d.get('id')}: {rule_type} needs {'2' if need == 2 else '3+'} points")
        return cls(
            id=d["id"],
            type=rule_type,
            name=d.get("name", d["id"]),
            points=[[float(x), float(y)] for x, y in points],
            classes=set(d.get("classes", ["person"])),
            severity=d.get("severity", DEFAULT_SEVERITY[rule_type]),
            cooldown_seconds=float(d.get("cooldown_seconds", 30)),
            direction=d.get("direction", "both"),
            dwell_seconds=float(d.get("dwell_seconds", 20)),
            max_count=int(d.get("max_count", 3)),
            min_confidence=float(d.get("min_confidence") or 0.0),
            schedule=d.get("schedule") or None,
            enabled=bool(d.get("enabled", True)),
        )

    def pixel_points(self, width: int, height: int) -> np.ndarray:
        return np.array([[x * width, y * height] for x, y in self.points]).round().astype(int)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def schedule_active(schedule: dict | None, ts: float) -> bool:
    """Is a rule with this schedule active at unix time `ts` (in this machine's local time)?

    start == end means all day. A window that crosses midnight (22:00-06:00) belongs to the day
    it starts on, so "Fri 22:00-06:00" is active until Saturday 06:00.
    """
    if not schedule:
        return True
    t = datetime.fromtimestamp(ts)
    days = set(schedule.get("days", range(7)))
    now, start, end = t.hour * 60 + t.minute, _minutes(schedule["start"]), _minutes(schedule["end"])
    today, yesterday = t.weekday(), (t.weekday() - 1) % 7
    if start == end:
        return today in days
    if start < end:
        return today in days and start <= now < end
    return (today in days and now >= start) or (yesterday in days and now < end)


@dataclass
class RuleHit:
    rule: Rule | None  # None for system events such as camera_offline
    event_type: str
    occurred_at: float
    track_id: int | None = None
    class_name: str | None = None
    confidence: float | None = None
    box: list[float] | None = None  # xyxy pixels
    details: dict = field(default_factory=dict)


class _RuleState:
    """Per-rule runtime state; rebuilt when the frame size or rule config changes."""

    def __init__(self, rule: Rule, width: int, height: int):
        self.rule = rule
        pts = rule.pixel_points(width, height)
        if rule.type in LINE_TYPES:
            self.line = sv.LineZone(sv.Point(*pts[0]), sv.Point(*pts[1]), triggering_anchors=ANCHOR)
        else:
            self.zone = sv.PolygonZone(pts, triggering_anchors=ANCHOR)
        self.inside: set[int] = set()  # track ids inside the zone on the previous update
        self.entered_at: dict[int, float] = {}  # loitering: track id -> time it entered
        self.loiter_reported: set[int] = set()
        self.over_capacity = False
        self.last_fired: dict[object, float] = {}  # cooldown key -> time

    def reset(self) -> None:
        self.inside.clear()
        self.entered_at.clear()
        self.loiter_reported.clear()
        self.over_capacity = False

    def cooled_down(self, key: object, now: float) -> bool:
        last = self.last_fired.get(key)
        if last is not None and now - last < self.rule.cooldown_seconds:
            return False
        self.last_fired[key] = now
        return True


class RuleEngine:
    def __init__(self, rules: list[Rule]):
        self.rules = rules
        self._states: list[_RuleState] = []
        self._size: tuple[int, int] | None = None
        self._crossings: list[tuple[Rule, str, str]] = []  # counting lines: (rule, class, in|out)

    def set_rules(self, rules: list[Rule]) -> None:
        self.rules = rules
        self._size = None  # force state rebuild

    def update(self, detections: sv.Detections, width: int, height: int, now: float) -> list[RuleHit]:
        if self._size != (width, height):
            self._states = [_RuleState(r, width, height) for r in self.rules if r.enabled]
            self._size = (width, height)
        if detections.tracker_id is None:
            return []

        names = np.array([COCO_CLASSES.get(int(c), str(c)) for c in detections.class_id])
        positions = object_positions(detections, height)
        hits: list[RuleHit] = []
        for state in self._states:
            if not schedule_active(state.rule.schedule, now):
                state.reset()  # so nobody "enters" or has been "loitering" the moment it switches on
                continue
            mask = np.isin(names, list(state.rule.classes)) if len(names) else np.zeros(0, bool)
            if state.rule.min_confidence and len(names):
                mask &= detections.confidence >= state.rule.min_confidence
            handler = getattr(self, f"_{state.rule.type}")
            hits.extend(handler(state, detections[mask], positions[mask], names[mask], now))
        return hits

    def take_crossings(self) -> list[tuple[Rule, str, str]]:
        """Counting-line crossings since the last call: (rule, class name, "in" | "out")."""
        out, self._crossings = self._crossings, []
        return out

    @staticmethod
    def _hit(state: _RuleState, event_type: str, now: float, dets: sv.Detections, names, i: int, **details):
        return RuleHit(
            rule=state.rule,
            event_type=event_type,
            occurred_at=now,
            track_id=int(dets.tracker_id[i]),
            class_name=str(names[i]),
            confidence=round(float(dets.confidence[i]), 3),
            box=[round(float(v), 1) for v in dets.xyxy[i]],
            details=details,
        )

    # Handlers get `dets` (real boxes, reported in events) and `pos` (one point per object,
    # used for all zone/line geometry; see object_positions).

    def _zone_entry(self, state, dets, pos, names, now):
        in_zone = state.zone.trigger(pos) if len(dets) else np.zeros(0, bool)
        current = {int(t) for t, inside in zip(dets.tracker_id, in_zone) if inside}
        hits = []
        for i, (tid, inside) in enumerate(zip(dets.tracker_id, in_zone)):
            tid = int(tid)
            if inside and tid not in state.inside and state.cooled_down(tid, now):
                hits.append(self._hit(state, "zone_entry", now, dets, names, i))
        state.inside = current
        return hits

    def _line_crossing(self, state, dets, pos, names, now):
        if not len(dets):
            state.line.trigger(pos)  # age crossing history
            return []
        crossed_in, crossed_out = state.line.trigger(pos)
        hits = []
        for i in range(len(dets)):
            for crossed, direction in ((crossed_in[i], "in"), (crossed_out[i], "out")):
                if not crossed or state.rule.direction not in (direction, "both"):
                    continue
                if state.cooled_down((int(dets.tracker_id[i]), direction), now):
                    hits.append(self._hit(state, "line_crossing", now, dets, names, i, direction=direction))
        return hits

    def _counting_line(self, state, dets, pos, names, now):
        crossed_in, crossed_out = state.line.trigger(pos)  # also ages history when empty
        for i in range(len(dets)):
            if crossed_in[i]:
                self._crossings.append((state.rule, str(names[i]), "in"))
            if crossed_out[i]:
                self._crossings.append((state.rule, str(names[i]), "out"))
        return []

    def _loitering(self, state, dets, pos, names, now):
        in_zone = state.zone.trigger(pos) if len(dets) else np.zeros(0, bool)
        current = set()
        hits = []
        for i, (tid, inside) in enumerate(zip(dets.tracker_id, in_zone)):
            tid = int(tid)
            if not inside:
                continue
            current.add(tid)
            entered = state.entered_at.setdefault(tid, now)
            dwell = now - entered
            if dwell >= state.rule.dwell_seconds and tid not in state.loiter_reported:
                if state.cooled_down(tid, now):
                    state.loiter_reported.add(tid)
                    hits.append(self._hit(state, "loitering", now, dets, names, i, dwell_seconds=round(dwell, 1)))
        # Leaving the zone resets the timer for that track.
        for tid in list(state.entered_at):
            if tid not in current:
                del state.entered_at[tid]
                state.loiter_reported.discard(tid)
        return hits

    def _occupancy(self, state, dets, pos, names, now):
        in_zone = state.zone.trigger(pos) if len(dets) else np.zeros(0, bool)
        count = int(in_zone.sum())
        if count <= state.rule.max_count:
            state.over_capacity = False  # re-arm once occupancy drops back
            return []
        if state.over_capacity or not state.cooled_down("zone", now):
            return []
        state.over_capacity = True
        idx = np.flatnonzero(in_zone)
        best = int(idx[dets.confidence[idx].argmax()])
        hit = self._hit(state, "occupancy_exceeded", now, dets, names, best, count=count, max_count=state.rule.max_count)
        hit.track_id, hit.details["track_ids"] = None, [int(dets.tracker_id[j]) for j in idx]
        return [hit]


def object_positions(detections: sv.Detections, frame_height: int, edge: float = 0.02) -> sv.Detections:
    """Where each object "is" on the floor plan, as zero-size boxes.

    Normally the bottom-center of the box (feet). If the box is cut off by the bottom of the
    frame the feet are not visible, so fall back to the box center instead of pinning the
    object to the frame edge. Zero-size boxes let PolygonZone/LineZone use this point as-is.
    """
    xyxy = detections.xyxy
    x = (xyxy[:, 0] + xyxy[:, 2]) / 2
    clipped = xyxy[:, 3] >= frame_height * (1 - edge)
    y = np.where(clipped, (xyxy[:, 1] + xyxy[:, 3]) / 2, xyxy[:, 3])
    points = np.stack([x, y, x, y], axis=1) if len(xyxy) else np.empty((0, 4), np.float32)
    return sv.Detections(
        xyxy=points.astype(np.float32),
        confidence=detections.confidence,
        class_id=detections.class_id,
        tracker_id=detections.tracker_id,
    )
