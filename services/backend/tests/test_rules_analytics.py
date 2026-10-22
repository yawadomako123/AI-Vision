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
    assert client.get("/api/settings").json() == {
        "event_retention_days": 30, "count_retention_days": 90,
        "clips_enabled": True, "clip_retention_days": 7, "clip_storage_mb": 1024, "sighting_retention_days": 14,
        "search_enabled": False, "analysis_fps": 3, "detection_sensitivity": "balanced",
        "motion_skip": True, "tamper_alerts": True}
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


def test_clip_ingest_retention_and_cap(client, camera, db_session, tmp_path):
    from app.retention import run_retention

    now = datetime.now(timezone.utc)
    day = f"{now:%Y/%m/%d}"
    (tmp_path / day).mkdir(parents=True)

    def add(ext, age_days, size_kb, shared_with=None):
        rel = shared_with or f"{day}/{ext}.mp4"
        if not shared_with:
            (tmp_path / rel).write_bytes(b"x" * size_kb * 1024)
        r = client.post("/api/internal/events", headers=WORKER, json=event_payload(
            camera["id"], ext_id=ext, occurred_at=(now - timedelta(days=age_days)).isoformat(),
            evidence={"snapshot_path": None, "clip_path": rel}))
        return r.json()

    e = add("fresh", 0, 100)
    assert e["clip_url"] == f"/media/events/{day}/fresh.mp4"
    add("shared", 0, 0, shared_with=f"{day}/fresh.mp4")  # two alerts, one clip
    add("week_old", 8, 100)  # older than the 7-day clip retention, newer than 30-day events
    add("big1", 1, 600)
    add("big2", 2, 600)

    assert client.get("/api/settings").json()["clip_storage_mb"] == 1024
    assert client.patch("/api/settings", json={"clip_storage_mb": 1}).status_code == 422  # API minimum is 50 MB
    with db_session() as db:
        from app.settings_store import update_settings
        update_settings(db, {"clip_storage_mb": 1})  # 1 MB so the test files exceed it
        db.commit()
        result = run_retention(db, tmp_path)
    # 1 MB cap: keep newest (fresh 100 KB + big1 600 KB), drop big2 (over cap) and week_old (expired)
    assert result["clips"] == 2
    events = {x["details"].get("ext", x["id"]): x for x in client.get("/api/events", params={"limit": 200}).json()["items"]}
    urls = sorted(x["clip_url"] or "-" for x in events.values())
    assert urls.count(f"/media/events/{day}/fresh.mp4") == 2 and f"/media/events/{day}/big1.mp4" in urls
    assert not (tmp_path / day / "big2.mp4").exists() and not (tmp_path / day / "week_old.mp4").exists()
    assert client.get("/api/events").json()["total"] == 5  # events themselves are kept


def test_worker_config(client):
    assert client.get("/api/internal/config", headers=WORKER).json() == {
        "clips": {"enabled": True}, "search_enabled": False, "analysis_fps": 3, "live_cameras": [], "detection_confidence": 0.5,
        "motion_skip": True, "tamper_alerts": True}
    client.patch("/api/settings", json={"clips_enabled": False, "analysis_fps": 2})
    cfg = client.get("/api/internal/config", headers=WORKER).json()
    assert cfg["clips"] == {"enabled": False} and cfg["analysis_fps"] == 2
    assert client.patch("/api/settings", json={"analysis_fps": 10}).status_code == 422


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


# --- entry/exit counting lines ----------------------------------------------------

COUNTER = {"type": "line_crossing", "alert": False, "name": "Front door", "points": [[0.5, 0], [0.5, 1]],
           "classes": ["person", "car"]}


def line_sample(camera_id, rule_id, minutes_ago, n_in, n_out, cls="person", name="Front door"):
    t = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).replace(second=0, microsecond=0)
    return {"camera_id": camera_id, "rule_id": rule_id, "rule_name": name, "bucket_start": t.isoformat(),
            "class_name": cls, "in_count": n_in, "out_count": n_out}


def test_counting_line_keeps_counting_while_disarmed(client, camera):
    url = f"/api/cameras/{camera['id']}/rules"
    assert client.post(url, json={**COUNTER, "points": [[0, 0], [1, 0], [1, 1]]}).status_code == 422
    client.post(url, json=ZONE)
    client.post(url, json=COUNTER)
    client.post(url, json={**COUNTER, "name": "Gate", "alert": True})
    feed = lambda: [(r["type"], r["alert"]) for r in client.get("/api/internal/rules", headers=WORKER).json()[str(camera["id"])]]
    assert feed() == [("zone_entry", True), ("line_crossing", False), ("line_crossing", True)]
    client.put("/api/arming", json={"armed": False})
    # Disarmed: lines keep counting, with alerts off; other rules aren't sent at all.
    assert feed() == [("line_crossing", False), ("line_crossing", False)]
    assert client.post(url, json={**COUNTER, "type": "counting_line"}).status_code == 422  # merged into lines
    client.put("/api/arming", json={"armed": True})


def test_entries_analytics_and_csv(client, camera, db_session):
    cam = camera["id"]
    rule = client.post(f"/api/cameras/{cam}/rules", json=COUNTER).json()
    r = client.post("/api/internal/line-counts", headers=WORKER, json=[
        line_sample(cam, rule["id"], 5, 3, 1),
        line_sample(cam, rule["id"], 5, 0, 2, cls="vehicle"),
        line_sample(cam, rule["id"], 65, 1, 0),
        line_sample(999, None, 5, 9, 9),  # unknown camera: ignored
        line_sample(cam, 424242, 5, 1, 1, name="=Old line"),  # unknown rule: kept by name
    ])
    assert r.status_code == 204
    assert client.post("/api/internal/line-counts", json=[]).status_code == 401  # worker only

    data = client.get("/api/analytics/entries", params={"hours": 24}).json()
    assert data["total"] == {"in": 5, "out": 4}
    door = next(l for l in data["lines"] if l["rule_id"] == rule["id"])
    assert (door["in_total"], door["out_total"]) == (4, 3)
    assert door["by_class"] == {"person": {"in": 4, "out": 1}, "vehicle": {"in": 0, "out": 2}}
    assert sum(door["in"]) == 4 and len(door["in"]) == len(data["buckets"])
    assert any(l["rule_id"] is None and l["rule_name"] == "=Old line" for l in data["lines"])

    # Deleting the rule keeps its history under the old name.
    client.delete(f"/api/rules/{rule['id']}")
    data = client.get("/api/analytics/entries", params={"hours": 24, "camera_id": cam}).json()
    assert {l["rule_name"]: l["in_total"] for l in data["lines"]} == {"Front door": 4, "=Old line": 1}

    csv_text = client.get("/api/analytics/entries.csv", params={"hours": 24, "bucket": "day"}).text
    lines = csv_text.strip().splitlines()
    assert lines[0] == "day_start_utc,camera,line,class,in,out"
    assert any(",'=Old line," in l for l in lines)  # formula injection guard

    from app.retention import run_retention
    client.post("/api/internal/line-counts", headers=WORKER, json=[line_sample(cam, None, 60 * 24 * 200, 1, 1)])
    with db_session() as db:
        assert run_retention(db, __import__("pathlib").Path("does-not-exist-storage"))["count_samples"] == 1


def test_detection_sensitivity_reaches_worker(client, operator):
    conf = lambda: client.get("/api/internal/config", headers=WORKER).json()["detection_confidence"]
    assert conf() == 0.5
    assert client.patch("/api/settings", json={"detection_sensitivity": "high"}).json()["detection_sensitivity"] == "high"
    assert conf() == 0.4
    assert client.patch("/api/settings", json={"detection_sensitivity": "extreme"}).status_code == 422
    assert operator.patch("/api/settings", json={"detection_sensitivity": "low"}).status_code == 403


def test_privacy_masks_always_reach_the_worker(client, camera):
    url = f"/api/cameras/{camera['id']}/rules"
    mask = {"type": "privacy_mask", "name": "Neighbour's window", "points": [[0.7, 0.1], [0.95, 0.1], [0.95, 0.4]]}
    assert client.post(url, json={**mask, "points": [[0, 0], [1, 1]]}).status_code == 422  # needs an area
    assert client.post(url, json=mask).status_code == 201
    client.post(url, json=ZONE)
    feed = lambda: sorted(r["type"] for r in client.get("/api/internal/rules", headers=WORKER).json()[str(camera["id"])])
    assert feed() == ["privacy_mask", "zone_entry"]
    client.put("/api/arming", json={"armed": False})
    assert feed() == ["privacy_mask"]  # disarmed: alerts stop, masks don't
    client.put("/api/arming", json={"armed": True})
