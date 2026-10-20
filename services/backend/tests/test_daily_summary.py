from datetime import datetime, timedelta, timezone

from app import daily_summary
from tests.conftest import WORKER
from tests.test_api import event_payload

ON = {"enabled": True, "topic": "aiv-t", "daily_summary": True, "summary_time": "08:00"}


def test_due_once_per_day_after_the_time():
    morning = datetime(2026, 10, 21, 7, 59)
    assert not daily_summary.due(ON, morning)
    assert daily_summary.due(ON, morning.replace(hour=8, minute=0))
    assert not daily_summary.due({**ON, "summary_last_sent": "2026-10-21"}, morning.replace(hour=9))
    assert daily_summary.due({**ON, "summary_last_sent": "2026-10-20"}, morning.replace(hour=9))
    assert not daily_summary.due({**ON, "enabled": False}, morning.replace(hour=9))
    assert not daily_summary.due({**ON, "daily_summary": False}, morning.replace(hour=9))


def test_summary_text(client, camera, db_session):
    now = datetime.now(timezone.utc)
    for i, (etype, ext) in enumerate([("zone_entry", "a"), ("zone_entry", "b"), ("loitering", "c")]):
        client.post("/api/internal/events", headers=WORKER,
                    json=event_payload(camera["id"], ext_id=ext, event_type=etype, occurred_at=(now - timedelta(hours=1)).isoformat()))
    client.post("/api/internal/events", headers=WORKER,
                json=event_payload(camera["id"], ext_id="old", occurred_at=(now - timedelta(days=3)).isoformat()))
    minute = (now - timedelta(minutes=30)).replace(second=0, microsecond=0).isoformat()
    client.post("/api/internal/line-counts", headers=WORKER, json=[{
        "camera_id": camera["id"], "rule_name": "Door", "bucket_start": minute, "class_name": "person",
        "in_count": 14, "out_count": 9}])
    client.post("/api/internal/counts", headers=WORKER, json=[{
        "camera_id": camera["id"], "bucket_start": minute, "class_name": "person", "avg_count": 1.2, "max_count": 4}])
    with db_session() as db:
        msg = daily_summary.build_summary(db, problems=[{"title": "Disk almost full"}])
    text = msg["message"]
    assert text.startswith("Last 24 h: 3 alerts (2 zone entry, 1 loitering), 3 to review")
    assert "14 in / 9 out" in text and "at most 4 people at once" in text
    assert "Needs attention: disk almost full." in text and msg["priority"] == 3


def test_quiet_day(db_session):
    with db_session() as db:
        msg = daily_summary.build_summary(db)
    assert msg["message"] == "Last 24 h: No alerts. No cameras set up." and msg["priority"] == 2


def test_maybe_send_records_the_day(client, db_session):
    client.patch("/api/settings/notifications", json={"enabled": True, "topic": "aiv-t", "summary_time": "06:30"})
    sent = []

    class FakeNotifier:
        def notify_system(self, cfg, title, message, **kw):
            sent.append(message)
            return True
    with db_session() as db:
        at = datetime(2026, 10, 21, 7, 0)
        assert daily_summary.maybe_send(db, FakeNotifier(), [], at) is True
        assert daily_summary.maybe_send(db, FakeNotifier(), [], at.replace(hour=9)) is False  # already sent today
    assert len(sent) == 1
    assert client.get("/api/settings/notifications").json()["summary_time"] == "06:30"
    assert client.patch("/api/settings/notifications", json={"summary_time": "25:00"}).status_code == 422


def test_send_now_endpoint(client, operator, monkeypatch):
    from app.routes import analytics
    assert operator.post("/api/settings/notifications/summary").status_code == 403
    assert client.post("/api/settings/notifications/summary").json()["ok"] is False  # no topic yet
    client.patch("/api/settings/notifications", json={"topic": "aiv-t"})
    got = []
    monkeypatch.setattr(analytics, "notifier_send", lambda cfg, msg: got.append(msg))
    assert client.post("/api/settings/notifications/summary").json()["ok"] is True
    assert got[0]["title"] == "Daily summary"
