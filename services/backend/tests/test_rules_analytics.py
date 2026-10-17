from datetime import datetime, timedelta, timezone

from tests.conftest import WORKER
from tests.test_api import event_payload

ZONE = {"type": "zone_entry", "name": "Door", "points": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.6]]}


# --- rules ---------------------------------------------------------------------

def test_rule_crud_and_worker_feed(client, camera):
    r = client.post(f"/api/cameras/{camera['id']}/rules", json=ZONE)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["classes"] == ["person"] and rule["severity"] == "medium"

    client.post(f"/api/cameras/{camera['id']}/rules",
                json={"type": "line_crossing", "name": "Gate", "points": [[0, 0.5], [1, 0.5]], "enabled": False})
    assert len(client.get(f"/api/cameras/{camera['id']}/rules").json()) == 2

    feed = client.get("/api/internal/rules", headers=WORKER).json()
    assert [r["name"] for r in feed[str(camera["id"])]] == ["Door"]  # disabled rules are not sent

    r = client.patch(f"/api/rules/{rule['id']}", json={"severity": "high", "points": [[0, 0], [1, 0], [1, 1]]})
    assert r.json()["severity"] == "high" and r.json()["points"] == [[0, 0], [1, 0], [1, 1]]
    assert client.delete(f"/api/rules/{rule['id']}").status_code == 204
    assert [r["name"] for r in client.get(f"/api/cameras/{camera['id']}/rules").json()] == ["Gate"]


def test_rule_validation(client, camera):
    url = f"/api/cameras/{camera['id']}/rules"
    assert client.post(url, json={**ZONE, "points": [[0, 0], [1.5, 0], [1, 1]]}).status_code == 422
    assert client.post(url, json={**ZONE, "points": [[0, 0], [1, 1]]}).status_code == 422
    assert client.post(url, json={**ZONE, "type": "line_crossing"}).status_code == 422
    assert client.post(url, json={**ZONE, "classes": ["dragon"]}).status_code == 422
    assert client.post("/api/cameras/999/rules", json=ZONE).status_code == 404
    rule = client.post(url, json=ZONE).json()
    assert client.patch(f"/api/rules/{rule['id']}", json={"points": [[0, 0]]}).status_code == 422


def test_operator_can_read_but_not_edit_rules(operator, client, camera):
    client.post(f"/api/cameras/{camera['id']}/rules", json=ZONE)
    assert len(operator.get(f"/api/cameras/{camera['id']}/rules").json()) == 1
    assert operator.post(f"/api/cameras/{camera['id']}/rules", json=ZONE).status_code == 403


def test_rule_min_confidence_roundtrip(client, camera):
    rule = client.post(f"/api/cameras/{camera['id']}/rules", json={**ZONE, "min_confidence": 0.6}).json()
    assert rule["min_confidence"] == 0.6
    assert client.patch(f"/api/rules/{rule['id']}", json={"min_confidence": 1.5}).status_code == 422
    feed = client.get("/api/internal/rules", headers=WORKER).json()
    assert feed[str(camera["id"])][0]["min_confidence"] == 0.6


def test_rule_schedule(client, camera):
    url = f"/api/cameras/{camera['id']}/rules"
    night = {"days": [4, 0, 4], "start": "22:00", "end": "06:00"}
    rule = client.post(url, json={**ZONE, "schedule": night}).json()
    assert rule["schedule"] == {"days": [0, 4], "start": "22:00", "end": "06:00"}  # de-duplicated, sorted
    assert client.get("/api/internal/rules", headers=WORKER).json()[str(camera["id"])][0]["schedule"]["start"] == "22:00"
    for bad in ({"days": [7], "start": "22:00", "end": "06:00"}, {"days": [], "start": "22:00", "end": "06:00"},
                {"days": [1], "start": "24:00", "end": "06:00"}, {"days": [1], "start": "9:00", "end": "10:00"}):
        assert client.post(url, json={**ZONE, "schedule": bad}).status_code == 422, bad
    cleared = client.patch(f"/api/rules/{rule['id']}", json={"schedule": None}).json()
    assert cleared["schedule"] is None


def test_arming(client, operator, camera):
    client.post(f"/api/cameras/{camera['id']}/rules", json=ZONE)
    assert client.get("/api/arming").json()["armed"] is True
    assert len(client.get("/api/internal/rules", headers=WORKER).json()) == 1

    r = operator.put("/api/arming", json={"armed": False}).json()  # operators may disarm
    assert r["armed"] is False and r["changed_by"] == "olivia"
    assert client.get("/api/internal/rules", headers=WORKER).json() == {}  # worker gets no rules
    actions = [a["action"] for a in client.get("/api/audit").json()["items"]]
    assert "system.disarm" in actions

    client.put("/api/arming", json={"armed": True})
    assert len(client.get("/api/internal/rules", headers=WORKER).json()) == 1


def test_arming_requires_login(anon):
    assert anon.get("/api/arming").status_code == 401
    assert anon.put("/api/arming", json={"armed": False}).status_code == 401


def test_rule_performance(client, camera):
    for i, status in enumerate(["false_alarm", "false_alarm", "resolved", None]):
        e = client.post("/api/internal/events", headers=WORKER,
                        json=event_payload(camera["id"], ext_id=f"p{i}", occurred_at=datetime.now(timezone.utc).isoformat())).json()
        if status:
            client.patch(f"/api/events/{e['id']}", json={"status": status})
    perf = client.get("/api/analytics/rules").json()
    assert len(perf) == 1
    r = perf[0]
    assert r["rule_name"] == "Front door" and r["total"] == 4 and r["unreviewed"] == 1
    assert r["false_alarm"] == 2 and r["false_alarm_rate"] == round(2 / 3, 3)


# --- counts & timeseries --------------------------------------------------------

def test_counts_and_timeseries(client, camera):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    samples = [
        {"camera_id": camera["id"], "bucket_start": (now - timedelta(minutes=m)).isoformat(),
         "class_name": "person", "avg_count": 1.5, "max_count": m % 4}
        for m in range(3)
    ] + [{"camera_id": 999, "bucket_start": now.isoformat(), "class_name": "car", "avg_count": 1, "max_count": 1}]
    assert client.post("/api/internal/counts", headers=WORKER, json=samples).status_code == 204
    client.post("/api/internal/events", headers=WORKER,
                json=event_payload(camera["id"], occurred_at=now.isoformat()))

    ts = client.get("/api/analytics/timeseries", params={"hours": 24}).json()
    assert len(ts["buckets"]) == 24
    assert "car" not in ts["counts"]  # unknown camera ignored
    person = ts["counts"]["person"]
    assert max(person["peak"]) == 2 and max(person["avg"]) == 1.5
    assert sum(ts["events"]["zone_entry"]) == 1

    days = client.get("/api/analytics/timeseries", params={"hours": 168, "bucket": "day"}).json()
    assert len(days["buckets"]) == 7


# --- settings, retention, system, CSV ------------------------------------------

def test_settings(client):
    assert client.get("/api/settings").json() == {"event_retention_days": 30, "count_retention_days": 90}
    assert client.patch("/api/settings", json={"event_retention_days": 7}).json()["event_retention_days"] == 7
    assert client.patch("/api/settings", json={"event_retention_days": 0}).status_code == 422


def test_retention_removes_old_events_and_snapshots(client, camera, db_session, tmp_path):
    from app.retention import run_retention

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=200)
    old_dir, new_dir = f"{old:%Y/%m/%d}", f"{now:%Y/%m/%d}"
    (tmp_path / old_dir).mkdir(parents=True)
    (tmp_path / old_dir / "old.jpg").write_bytes(b"x")
    (tmp_path / new_dir).mkdir(parents=True)
    (tmp_path / new_dir / "new.jpg").write_bytes(b"x")
    client.post("/api/internal/events", headers=WORKER, json=event_payload(
        camera["id"], ext_id="old", occurred_at=old.isoformat(), evidence={"snapshot_path": f"{old_dir}/old.jpg"}))
    client.post("/api/internal/events", headers=WORKER, json=event_payload(
        camera["id"], ext_id="new", occurred_at=now.isoformat(), evidence={"snapshot_path": f"{new_dir}/new.jpg"}))

    with db_session() as db:
        result = run_retention(db, tmp_path)  # default retention: 30 days
    assert result["events"] == 1
    assert not (tmp_path / old_dir).exists()
    assert (tmp_path / new_dir / "new.jpg").exists()
    assert client.get("/api/events").json()["total"] == 1


def test_system_health(client):
    client.get("/api/internal/cameras", headers=WORKER)
    s = client.get("/api/system").json()
    assert s["database"] == "ok" and s["worker"]["connected"] is True
    assert s["storage"]["disk_free_bytes"] > 0


def test_csv_export(client, camera):
    client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"]))
    e = client.get("/api/events").json()["items"][0]
    client.patch(f"/api/events/{e['id']}", json={"status": "resolved", "note": "=HYPERLINK(\"evil\")"})
    r = client.get("/api/events/export.csv", params={"status": "resolved"})
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,occurred_at,camera")
    assert len(lines) == 2 and "Front door" in lines[1]
    assert "'=HYPERLINK" in lines[1]  # formula injection neutralized
