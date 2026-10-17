import base64
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tests.conftest import WORKER
from tests.test_api import event_payload

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0fakejpeg").decode()
DIM = 64


def vec(*hot: int, noise: float = 0.0) -> np.ndarray:
    v = np.zeros(DIM, np.float32)
    for h in hot:
        v[h] = 1.0
    v += noise
    return v / np.linalg.norm(v)


def b64(v: np.ndarray) -> str:
    return base64.b64encode(v.astype(np.float16).tobytes()).decode()


def post(client, camera_id, v, cls="person", minutes_ago=5, track=1, duration=30):
    first = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    r = client.post("/api/internal/sightings", headers=WORKER, json={
        "camera_id": camera_id, "track_id": track, "object_class": cls,
        "first_seen": first.isoformat(), "last_seen": (first + timedelta(seconds=duration)).isoformat(),
        "confidence": 0.9, "path": [[0.1, 0.9], [0.5, 0.8]], "embedding": b64(v),
        "thumbnail": JPEG, "context": JPEG})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def fake_text(monkeypatch):
    from app.routes import sightings

    vocab = {"white hoodie": vec(0), "red car": vec(1)}
    monkeypatch.setattr(sightings.text_encoder, "encode", lambda q: vocab.get(q, vec(5)))
    # These fake vectors are exact matches (cosine 1), so widen the text relevance band for the test.
    monkeypatch.setattr(sightings, "text_relevance", lambda c: float(np.clip(c, 0, 1)))
    return vocab


def test_search_ranks_by_meaning(client, camera, fake_text):
    hoodie = post(client, camera["id"], vec(0, noise=0.01), track=1)
    car = post(client, camera["id"], vec(1, noise=0.01), cls="car", track=2)
    res = client.get("/api/search", params={"q": "white hoodie"}).json()
    assert [r["id"] for r in res][:2] == [hoodie, car]
    assert res[0]["score"] > res[1]["score"]
    assert res[0]["thumbnail_url"].startswith("/media/events/sightings/")
    assert res[0]["duration_seconds"] == 30 and res[0]["camera_name"] == "Front door"
    # filters
    assert [r["id"] for r in client.get("/api/search", params={"q": "white hoodie", "object_class": "vehicle"}).json()] == [car]
    assert client.get("/api/search", params={"q": "white hoodie", "hours": 0}).status_code == 422


def test_empty_query_lists_recent(client, camera):
    older = post(client, camera["id"], vec(0), minutes_ago=60)
    newer = post(client, camera["id"], vec(1), minutes_ago=1)
    assert [r["id"] for r in client.get("/api/search").json()] == [newer, older]


def test_similar_is_a_time_ordered_timeline(client, camera):
    cam2 = client.post("/api/cameras", json={"name": "Back door", "stream_url": "rtsp://h/2"}).json()
    a = post(client, camera["id"], vec(0, 3), minutes_ago=30)
    b = post(client, cam2["id"], vec(0, 3, noise=0.02), minutes_ago=10)  # looks the same, other camera
    post(client, camera["id"], vec(1, 2), minutes_ago=20)  # looks different
    timeline = client.get(f"/api/sightings/{a}/similar").json()
    assert [t["id"] for t in timeline] == [a, b]  # oldest first
    assert {t["camera_name"] for t in timeline} == {"Front door", "Back door"}


def test_detail_links_alert_clips(client, camera):
    s = post(client, camera["id"], vec(0), minutes_ago=5, duration=60)
    during = datetime.now(timezone.utc) - timedelta(minutes=4, seconds=30)
    client.post("/api/internal/events", headers=WORKER, json=event_payload(
        camera["id"], ext_id="during", occurred_at=during.isoformat(),
        evidence={"snapshot_path": "a.jpg", "clip_path": "a.mp4"}))
    client.post("/api/internal/events", headers=WORKER, json=event_payload(
        camera["id"], ext_id="later", occurred_at=datetime.now(timezone.utc).isoformat()))
    d = client.get(f"/api/sightings/{s}").json()
    assert d["path"] == [[0.1, 0.9], [0.5, 0.8]]
    assert [c["clip_url"] for c in d["clips"]] == ["/media/events/a.mp4"]


def test_ingest_validation_and_auth(client, anon, camera):
    good = {"camera_id": camera["id"], "track_id": 1, "object_class": "person",
            "first_seen": "2026-10-17T10:00:00Z", "last_seen": "2026-10-17T10:00:30Z", "confidence": 0.9,
            "path": [], "embedding": b64(vec(0)), "thumbnail": JPEG, "context": JPEG}
    assert client.post("/api/internal/sightings", json=good).status_code == 401  # needs the service token
    bad_img = {**good, "thumbnail": base64.b64encode(b"<svg/>").decode()}
    assert client.post("/api/internal/sightings", headers=WORKER, json=bad_img).status_code == 422
    tiny = {**good, "embedding": base64.b64encode(np.ones(4, np.float16).tobytes()).decode()}
    assert client.post("/api/internal/sightings", headers=WORKER, json=tiny).status_code == 422
    assert client.post("/api/internal/sightings", headers=WORKER, json={**good, "camera_id": 999}).status_code == 404
    assert anon.get("/api/search", params={"q": "x"}).status_code == 401


def test_search_unavailable_without_model(client, camera, monkeypatch):
    from app.routes import sightings
    from app.search_engine import TextEncoder

    monkeypatch.setattr(sightings, "text_encoder", TextEncoder(__import__("pathlib").Path("does-not-exist")))
    post(client, camera["id"], vec(0))
    assert client.get("/api/search", params={"q": "anything"}).status_code == 503
    assert client.get("/api/search/status").json() == {"available": False}


def test_sighting_retention(client, camera, db_session, tmp_path):
    from app.retention import run_retention

    post(client, camera["id"], vec(0), minutes_ago=60 * 24 * 20)  # 20 days old (> 14-day default)
    post(client, camera["id"], vec(1), minutes_ago=5)
    with db_session() as db:
        assert run_retention(db, tmp_path)["sightings"] == 1
    assert len(client.get("/api/search", params={"hours": 24 * 365}).json()) == 1


def test_tokenizer_matches_official_when_model_present():
    """The export script stores the official tokenizer's ids for test phrases; ours must match."""
    import json
    from pathlib import Path

    from app.config import settings
    from app.siglip_tokenizer import SiglipTokenizer

    parity = settings.search_model_dir / "tokenizer_parity.json"
    if not parity.exists():
        pytest.skip("search model not exported")
    meta = json.loads((settings.search_model_dir / "siglip.json").read_text())
    tok = SiglipTokenizer(Path(settings.search_model_dir) / "spiece.model", meta["eos_id"], meta["pad_id"], meta["max_length"])
    for case in json.loads(parity.read_text(encoding="utf-8")):
        assert tok.encode(case["text"]) == case["ids"], case["text"]
