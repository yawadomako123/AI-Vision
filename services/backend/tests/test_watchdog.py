from datetime import datetime, timezone

from app import watchdog as wd

GB = 1024**3
CFG = {"enabled": True, "topic": "t", "server": "https://ntfy.example"}


def at(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


def test_worker_down_alerts_once_and_clears(monkeypatch):
    sent = []
    monkeypatch.setattr(wd.notifier, "notify_system", lambda cfg, title, msg, **kw: sent.append(title) or True)
    w = wd.Watchdog(started_at=0)
    healthy = (100 * GB, 500 * GB)

    keys = lambda now, last: [p.key for p in w.evaluate(now, last, *healthy)]
    assert keys(1000, at(990)) == []
    assert keys(1000, at(800)) == ["worker_down"]  # silent for over 2 min
    assert keys(100, None) == []  # still starting up
    assert keys(1000, None) == ["worker_down"]  # never connected

    started, cleared = w.update(w.evaluate(1000, at(800), *healthy))
    assert [p.key for p in started] == ["worker_down"] and cleared == []
    assert w.update(w.evaluate(1030, at(800), *healthy)) == ([], [])  # no repeat while it lasts
    started, cleared = w.update(w.evaluate(1060, at(1058), *healthy))
    assert started == [] and [p.key for p in cleared] == ["worker_down"]


def test_disk_low_thresholds():
    w = wd.Watchdog(started_at=0)
    keys = lambda free, total: [p.key for p in w.evaluate(10, at(9), free, total)]
    assert keys(1 * GB, 500 * GB) == ["disk_low"]  # under 2 GB
    assert keys(20 * GB, 1000 * GB) == ["disk_low"]  # under 5 %
    assert keys(60 * GB, 1000 * GB) == []


def test_check_pushes_start_and_recovery(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(wd.notifier, "notify_system", lambda cfg, title, msg, **kw: sent.append(title) or True)
    w = wd.Watchdog(started_at=0)
    w.check(tmp_path, at(1), CFG)  # worker last seen at t=1: long gone
    assert sent and "worker" in sent[0].lower() and w.status()[0]["key"] == "worker_down"
    w.check(tmp_path, datetime.now(timezone.utc), CFG)
    assert sent[-1] == "Back to normal" and w.status() == []


def test_system_messages_ignore_min_severity_but_need_notifications_on():
    n = wd.notifier
    assert n.notify_system({**CFG, "enabled": False}, "x", "y") is False
    assert n.notify_system({**CFG, "topic": ""}, "x", "y") is False


def test_system_endpoint_lists_problems(client, monkeypatch):
    monkeypatch.setattr(wd.watchdog, "active", {"disk_low": wd.Problem("disk_low", "Disk almost full", "1 GB free")})
    assert client.get("/api/system").json()["problems"] == [
        {"key": "disk_low", "title": "Disk almost full", "message": "1 GB free"}]
