import base64
import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from app.plates import normalize, similarity
from tests.conftest import WORKER
from tests.test_search import b64, vec


def jpeg(color=(200, 200, 200)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (60, 20), color).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def sighting(client, camera_id, plate, conf=0.9, minutes_ago=2, track=1, cls="car"):
    first = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    r = client.post("/api/internal/sightings", headers=WORKER, json={
        "camera_id": camera_id, "track_id": track, "object_class": cls, "first_seen": first.isoformat(),
        "last_seen": (first + timedelta(seconds=8)).isoformat(), "confidence": 0.9, "path": [],
        "embedding": b64(vec(track % 30)), "thumbnail": jpeg(), "context": jpeg(),
        "plate": plate, "plate_confidence": conf, "plate_image": jpeg((255, 255, 255))})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture(autouse=True)
def plates_on(client):
    client.patch("/api/settings", json={"search_enabled": True, "plates_enabled": True})


def test_similarity_tolerates_misreads_and_partials():
    assert normalize("gr 1234-23") == "GR123423"
    assert similarity("GR 1234-23", "GR123423") == 1.0
    assert similarity("GR123423", "GR12342B") >= 0.85       # one misread (3 -> B looks like 8? still close)
    assert similarity("GRO23", "GR023") == 1.0               # O vs 0 counted the same
    assert similarity("GR12", "GR123423") == 0.9             # partial plate
    assert similarity("GR123423", "AS998877") < 0.6


def test_plate_stored_with_picture_and_searchable(client, camera):
    sighting(client, camera["id"], "gr 1234-23", track=1)
    sighting(client, camera["id"], "AS 9988-21", track=2)
    hits = client.get("/api/search", params={"plate": "GR12342"}).json()
    assert [h["plate"] for h in hits] == ["GR123423"]
    assert hits[0]["plate_url"].endswith("_p.jpg") and client.get(hits[0]["plate_url"]).status_code == 200
    assert client.get("/api/search", params={"plate": "GR123428"}).json()[0]["plate"] == "GR123423"  # one char off


def test_plates_ignored_while_switched_off(client, camera):
    client.patch("/api/settings", json={"plates_enabled": False})
    sid = sighting(client, camera["id"], "GR123423")
    s = client.get(f"/api/sightings/{sid}").json()
    assert s["plate"] is None and s["plate_url"] is None


def test_watchlist_alerts_and_names(client, operator, camera, monkeypatch):
    from app.routes import internal
    pushed = []
    monkeypatch.setattr(internal.notifier, "notify", lambda e, cfg: pushed.append(e["event_type"]))
    assert operator.post("/api/plates/watchlist", json={"plate": "X1", "label": "x"}).status_code == 403
    r = client.post("/api/plates/watchlist", json={"plate": "gr 1234-23", "label": "Stolen Corolla"})
    assert r.status_code == 201 and r.json()["plate"] == "GR123423"
    assert client.post("/api/plates/watchlist", json={"plate": "GR123423", "label": "dup"}).status_code == 409
    client.post("/api/plates/watchlist", json={"plate": "AS998821", "label": "Manager's car", "alert": False})

    sighting(client, camera["id"], "GR123428", track=5)  # read with one wrong character
    sighting(client, camera["id"], "AS998821", track=6)  # named, but no alert
    events = client.get("/api/events", params={"event_type": "plate_watchlist"}).json()["items"]
    assert len(events) == 1 and events[0]["rule_name"] == "Stolen Corolla" and events[0]["severity"] == "high"
    assert events[0]["details"]["plate"] == "GR123428" and events[0]["snapshot_url"]
    assert pushed == ["plate_watchlist"]
    named = client.get("/api/search", params={"plate": "AS998821"}).json()[0]
    assert named["plate_label"] == "Manager's car"
    wl = {w["label"]: w for w in client.get("/api/plates/watchlist").json()}
    assert wl["Manager's car"]["last_seen"] is not None
    assert client.delete(f"/api/plates/watchlist/{wl['Stolen Corolla']['id']}").status_code == 204
    actions = [a["action"] for a in client.get("/api/audit").json()["items"]]
    assert "plate.watch" in actions and "plate.unwatch" in actions
