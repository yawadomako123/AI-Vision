import json
from pathlib import Path

import httpx

from app.notifier import Notifier, build_message, send, should_notify
from tests.conftest import WORKER
from tests.test_api import event_payload

EVENT = {"id": 7, "camera_id": 1, "camera_name": "Front door", "event_type": "zone_entry", "severity": "high",
         "rule_id": "r1", "rule_name": "Door", "object_class": "person", "confidence": 0.93, "snapshot_path": None}
CFG = {"enabled": True, "server": "https://ntfy.example", "topic": "aiv-test", "min_severity": "high",
       "include_snapshot": False, "dashboard_url": "https://box.example", "token_encrypted": None,
       "cooldown_seconds": 60}


def recording_client(seen: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "x"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_message_content():
    msg = build_message(EVENT, CFG)
    assert msg["title"] == "Zone entry - Front door"
    assert "rule: Door" in msg["message"] and "person (93%)" in msg["message"]
    assert msg["priority"] == 4
    assert msg["click"] == "https://box.example/events?open=7"


def test_severity_filter_and_disabled():
    assert should_notify(EVENT, CFG)
    assert not should_notify({**EVENT, "severity": "medium"}, CFG)
    assert should_notify({**EVENT, "severity": "medium"}, {**CFG, "min_severity": "medium"})
    assert not should_notify(EVENT, {**CFG, "enabled": False})
    assert not should_notify(EVENT, {**CFG, "topic": ""})
    assert not should_notify({**EVENT, "event_type": "camera_online"}, {**CFG, "min_severity": "low"})


def test_send_json_publish():
    seen = []
    send(CFG, build_message(EVENT, CFG), client=recording_client(seen))
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://ntfy.example"
    body = json.loads(req.content)
    assert body["topic"] == "aiv-test" and body["priority"] == 4


def test_send_with_snapshot_and_unicode_camera(tmp_path: Path):
    snap = tmp_path / "s.jpg"
    snap.write_bytes(b"\xff\xd8jpeg")
    seen = []
    msg = build_message({**EVENT, "camera_name": "Entrée"}, CFG)
    send(CFG, msg, snapshot=snap, client=recording_client(seen))
    req = seen[0]
    assert req.method == "PUT" and str(req.url) == "https://ntfy.example/aiv-test"
    assert req.content == b"\xff\xd8jpeg"
    assert req.headers["Title"].startswith("=?UTF-8?B?")  # non-latin-1 safely encoded
    assert req.headers["Filename"] == "s.jpg"


def test_rejected_snapshot_falls_back_to_text(tmp_path: Path):
    snap = tmp_path / "s.jpg"
    snap.write_bytes(b"\xff\xd8jpeg")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            return httpx.Response(400, json={"error": "attachments not allowed"})
        return httpx.Response(200, json={"id": "x"})

    send(CFG, build_message(EVENT, CFG), snapshot=snap, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [r.method for r in seen] == ["PUT", "POST"]
    assert json.loads(seen[1].content)["title"] == "Zone entry - Front door"


def test_cooldown_per_camera_and_rule(tmp_path):
    n = Notifier(tmp_path)
    assert n.notify(EVENT, CFG) is True
    assert n.notify({**EVENT, "id": 8}, CFG) is False  # same camera+rule within cooldown
    assert n.notify({**EVENT, "id": 9, "rule_id": "r2"}, CFG) is True
    assert n.notify({**EVENT, "id": 10}, {**CFG, "cooldown_seconds": 0}) is True


def test_settings_api(client):
    r = client.get("/api/settings/notifications").json()
    assert r["enabled"] is False and r["has_token"] is False
    r = client.patch("/api/settings/notifications", json={
        "enabled": True, "topic": "aiv-abc_123", "min_severity": "medium", "token": "tk_secret"}).json()
    assert r["enabled"] and r["topic"] == "aiv-abc_123" and r["has_token"] is True
    assert "tk_secret" not in json.dumps(r)
    assert client.patch("/api/settings/notifications", json={"topic": "bad topic!"}).status_code == 422
    assert client.patch("/api/settings/notifications", json={"server": "ftp://x"}).status_code == 422
    audit = json.dumps(client.get("/api/audit").json())
    assert "tk_secret" not in audit and "aiv-abc_123" not in audit  # secrets stay out of the audit log


def test_settings_admin_only(operator):
    assert operator.get("/api/settings/notifications").status_code == 403
    assert operator.post("/api/settings/notifications/test").status_code == 403


def test_ingest_queues_notification(client, camera, monkeypatch):
    from app.routes import internal

    queued = []
    monkeypatch.setattr(internal.notifier, "notify", lambda event, cfg: queued.append((event, cfg)))
    client.patch("/api/settings/notifications", json={"enabled": True, "topic": "aiv-t"})
    client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"]))
    assert len(queued) == 1
    event, cfg = queued[0]
    assert event["event_type"] == "zone_entry" and event["snapshot_path"] == "2026/10/16/x.jpg"
    assert cfg["topic"] == "aiv-t"


def test_test_endpoint_reports_errors(client, monkeypatch):
    from app.routes import analytics

    assert client.post("/api/settings/notifications/test").json() == {"ok": False, "error": "set a topic first"}
    client.patch("/api/settings/notifications", json={"topic": "aiv-t"})

    def boom(cfg, msg):
        raise httpx.ConnectError("unreachable")
    monkeypatch.setattr(analytics, "notifier_send", boom)
    r = client.post("/api/settings/notifications/test").json()
    assert r["ok"] is False and "unreachable" in r["error"]
    monkeypatch.setattr(analytics, "notifier_send", lambda cfg, msg: None)
    assert client.post("/api/settings/notifications/test").json()["ok"] is True
