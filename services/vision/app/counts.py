"""Per-minute analytics: people/vehicle counts (average and peak seen at the same time) and
entry/exit totals from counting lines."""

from datetime import datetime, timezone

GROUPS = {
    "person": "person",
    "car": "vehicle", "motorcycle": "vehicle", "bus": "vehicle", "truck": "vehicle", "bicycle": "vehicle",
}
REPORTED = ("person", "vehicle")


class MinuteCounter:
    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self._minute: datetime | None = None
        self._sum = dict.fromkeys(REPORTED, 0)
        self._max = dict.fromkeys(REPORTED, 0)
        self._n = 0

    def add(self, class_names: list[str], at: float) -> list[dict]:
        """Record one analysed frame. Returns finished samples when a minute rolls over."""
        minute = datetime.fromtimestamp(at, timezone.utc).replace(second=0, microsecond=0)
        done = self.flush() if self._minute is not None and minute != self._minute else []
        self._minute = minute
        frame = dict.fromkeys(REPORTED, 0)
        for name in class_names:
            group = GROUPS.get(name)
            if group:
                frame[group] += 1
        for g in REPORTED:
            self._sum[g] += frame[g]
            self._max[g] = max(self._max[g], frame[g])
        self._n += 1
        return done

    def flush(self) -> list[dict]:
        if self._minute is None or self._n == 0:
            return []
        samples = [
            {"camera_id": self.camera_id, "bucket_start": self._minute.isoformat(), "class_name": g,
             "avg_count": round(self._sum[g] / self._n, 3), "max_count": self._max[g]}
            for g in REPORTED
        ]
        self._sum = dict.fromkeys(REPORTED, 0)
        self._max = dict.fromkeys(REPORTED, 0)
        self._n = 0
        return samples


class LineCounter:
    """Per-minute in/out totals per counting line. Only lines that someone crossed are reported,
    so a quiet camera sends nothing."""

    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self._minute: datetime | None = None
        self._tally: dict[tuple[str, str, str], dict[str, int]] = {}

    def add(self, crossings: list, at: float) -> list[dict]:
        """crossings: (rule, class name, "in" | "out") from RuleEngine.take_crossings().
        Call every analysed frame; returns finished samples when a minute rolls over."""
        minute = datetime.fromtimestamp(at, timezone.utc).replace(second=0, microsecond=0)
        done = self.flush() if self._minute is not None and minute != self._minute else []
        self._minute = minute
        for rule, name, direction in crossings:
            key = (rule.id, rule.name, GROUPS.get(name, name))
            self._tally.setdefault(key, {"in": 0, "out": 0})[direction] += 1
        return done

    def flush(self) -> list[dict]:
        if self._minute is None or not self._tally:
            return []
        samples = [
            {"camera_id": self.camera_id, "rule_id": _rule_number(rule_id), "rule_name": rule_name,
             "bucket_start": self._minute.isoformat(), "class_name": group,
             "in_count": t["in"], "out_count": t["out"]}
            for (rule_id, rule_name, group), t in self._tally.items()
        ]
        self._tally = {}
        return samples


def _rule_number(rule_id: str) -> int | None:
    """API rules arrive as "rule<id>"."""
    n = str(rule_id).removeprefix("rule")
    return int(n) if n.isdigit() else None
