from datetime import datetime, timezone

from app.counts import MinuteCounter


def ts(minute: int, second: int) -> float:
    return datetime(2026, 10, 16, 10, minute, second, tzinfo=timezone.utc).timestamp()


def test_minute_rollover_emits_avg_and_peak():
    c = MinuteCounter(camera_id=3)
    assert c.add(["person", "person", "car"], ts(0, 1)) == []
    assert c.add(["person", "truck", "bicycle", "dog"], ts(0, 30)) == []
    samples = c.add([], ts(1, 0))  # next minute: previous one is finished
    by_class = {s["class_name"]: s for s in samples}
    assert by_class["person"] == {"camera_id": 3, "bucket_start": "2026-10-16T10:00:00+00:00",
                                  "class_name": "person", "avg_count": 1.5, "max_count": 2}
    assert by_class["vehicle"]["avg_count"] == 1.5 and by_class["vehicle"]["max_count"] == 2
    # The empty frame belongs to minute 1.
    assert {s["max_count"] for s in c.flush()} == {0}
