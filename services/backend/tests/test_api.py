from tests.conftest import WORKER


def event_payload(camera_id, ext_id="20261016T101500_cam1_zone_entry_abc123", **over):
    body = {
        "id": ext_id,
        "camera_id": camera_id,
        "event_type": "zone_entry",
        "occurred_at": "2026-10-16T10:15:00Z",
        "severity": "high",
        "rule": {"id": "door", "name": "Front door", "type": "zone_entry"},
        "object": {"class": "person", "track_id": 4, "confidence": 0.91, "box": [1, 2, 3, 4]},
        "details": {},
        "evidence": {"snapshot_path": "2026/10/16/x.jpg"},
        "status": "unreviewed",
    }
    return {**body, **over}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# --- cameras -----------------------------------------------------------------

def test_camera_password_is_masked_everywhere_public(client, camera):
    assert camera["stream_url_masked"] == "rtsp://admin:***@10.0.0.5:554/s1"
    for body in (client.get("/api/cameras").text, client.get(f"/api/cameras/{camera['id']}").text):
        assert "s3cret" not in body


def test_camera_password_encrypted_at_rest(client, camera):
    from sqlalchemy import select

    from app.db import get_db
    from app.main import app
    from app.models import Camera

    db = next(app.dependency_overrides[get_db]())
    stored = db.scalar(select(Camera.stream_url_encrypted))
    assert "s3cret" not in stored and "10.0.0.5" not in stored


def test_camera_validation_and_duplicates(client, camera):
    assert client.post("/api/cameras", json={"name": "x", "stream_url": "file:///etc/passwd"}).status_code == 422
    assert client.post("/api/cameras", json={"name": "Front door", "stream_url": "rtsp://h/s"}).status_code == 409


def test_camera_update_and_delete(client, camera):
    r = client.patch(f"/api/cameras/{camera['id']}", json={"enabled": False, "stream_url": "rtsp://10.0.0.6/s2"})
    assert r.json()["enabled"] is False
    assert r.json()["stream_url_masked"] == "rtsp://10.0.0.6/s2"
    assert client.delete(f"/api/cameras/{camera['id']}").status_code == 204
    assert client.get(f"/api/cameras/{camera['id']}").status_code == 404


def test_camera_test_reports_failure_without_leaking_url(client):
    cam = client.post("/api/cameras", json={"name": "dead", "stream_url": "rtsp://u:topsecret@127.0.0.1:1/x"}).json()
    r = client.post(f"/api/cameras/{cam['id']}/test").json()
    assert r["ok"] is False
    assert "topsecret" not in str(r)


# --- worker (internal) API --------------------------------------------------

def test_internal_requires_service_token(client, camera):
    assert client.get("/api/internal/cameras").status_code == 401
    assert client.get("/api/internal/cameras", headers={"X-Service-Token": "wrong"}).status_code == 401


def test_worker_gets_real_stream_url(client, camera):
    cams = client.get("/api/internal/cameras", headers=WORKER).json()
    assert cams == [{"id": camera["id"], "name": "Front door",
                     "stream_url": "rtsp://admin:s3cret@10.0.0.5:554/s1", "enabled": True}]


def test_worker_status_report(client, camera):
    r = client.post(f"/api/internal/cameras/{camera['id']}/status", headers=WORKER,
                    json={"status": "online", "last_frame_at": "2026-10-16T10:00:00Z", "infer_fps": 2.9})
    assert r.status_code == 204
    cam = client.get(f"/api/cameras/{camera['id']}").json()
    assert cam["status"] == "online" and cam["infer_fps"] == 2.9


# --- events --------------------------------------------------------------------

def test_event_ingest_and_read(client, camera):
    r = client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"]))
    assert r.status_code == 201
    e = r.json()
    assert e["camera_name"] == "Front door"
    assert e["object_class"] == "person" and e["track_id"] == 4
    assert e["snapshot_url"] == "/media/events/2026/10/16/x.jpg"
    assert e["status"] == "unreviewed"
    assert client.get(f"/api/events/{e['id']}").json()["rule_name"] == "Front door"


def test_event_ingest_is_idempotent(client, camera):
    first = client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"])).json()
    again = client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"])).json()
    assert first["id"] == again["id"]
    assert client.get("/api/events").json()["total"] == 1


def test_event_for_unknown_camera(client):
    assert client.post("/api/internal/events", headers=WORKER, json=event_payload(999)).status_code == 404


def test_system_event_without_object(client, camera):
    body = event_payload(camera["id"], ext_id="off1", event_type="camera_offline", rule=None,
                         object=None, evidence=None, details={"offline_seconds": 15.0})
    e = client.post("/api/internal/events", headers=WORKER, json=body).json()
    assert e["object_class"] is None and e["snapshot_url"] is None


def test_event_filters_and_paging(client, camera):
    for i, (etype, ts) in enumerate([("zone_entry", "2026-10-16T10:00:00Z"),
                                     ("loitering", "2026-10-16T11:00:00Z"),
                                     ("zone_entry", "2026-10-16T12:00:00Z")]):
        client.post("/api/internal/events", headers=WORKER,
                    json=event_payload(camera["id"], ext_id=f"e{i}", event_type=etype, occurred_at=ts))
    page = client.get("/api/events", params={"event_type": "zone_entry"}).json()
    assert page["total"] == 2
    assert [e["occurred_at"][:13] for e in page["items"]] == ["2026-10-16T12", "2026-10-16T10"]  # newest first
    assert client.get("/api/events", params={"since": "2026-10-16T10:30:00Z"}).json()["total"] == 2
    assert len(client.get("/api/events", params={"limit": 1}).json()["items"]) == 1


def test_event_review_workflow(client, camera):
    e = client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"])).json()
    r = client.patch(f"/api/events/{e['id']}", json={"status": "resolved", "note": "delivery", "reviewed_by": "ama"})
    assert r.json()["status"] == "resolved" and r.json()["reviewed_at"] is not None
    assert client.patch(f"/api/events/{e['id']}", json={"status": "deleted"}).status_code == 422
    assert client.get("/api/events", params={"status": "resolved"}).json()["total"] == 1


def test_deleting_camera_deletes_its_events(client, camera):
    client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"]))
    client.delete(f"/api/cameras/{camera['id']}")
    assert client.get("/api/events").json()["total"] == 0


def test_summary(client, camera):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"], occurred_at=now))
    s = client.get("/api/analytics/summary").json()
    assert s["cameras"]["total"] == 1
    assert s["events"]["by_type"] == {"zone_entry": 1}
    assert s["unreviewed"] == 1


# --- live updates --------------------------------------------------------------

def test_websocket_receives_new_events(client, camera):
    with client.websocket_connect("/api/ws") as ws:
        client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"]))
        msg = ws.receive_json()
    assert msg["type"] == "event"
    assert msg["data"]["event_type"] == "zone_entry"
