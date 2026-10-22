import os
import time
from datetime import datetime, timedelta, timezone

import httpx

from app import recording
from app.recording import camera_path, desired_paths, enforce_cap, worker_url


def test_tests_never_touch_the_real_mediamtx():
    assert recording.mediamtx.enabled is False


def test_which_path_carries_each_camera():
    assert camera_path(3, "rtsp://127.0.0.1:8554/cam1") == recording.CameraPath("cam1", False, None)
    assert camera_path(3, "rtsp://mediamtx:8554/cam2").name == "cam2"
    ip = camera_path(7, "rtsp://admin:pw@192.168.1.64:554/Streaming/Channels/102")
    assert ip.proxied and ip.name == "aiv7" and ip.source.startswith("rtsp://admin:pw@192.168.1.64")
    assert camera_path(1, "https://example.org/live.m3u8") is None  # HLS test feeds stay direct
    assert camera_path(1, "rtsp://127.0.0.1:8554/") is None


def test_desired_paths_and_worker_url(monkeypatch):
    cams = [(1, "rtsp://127.0.0.1:8554/cam1", True), (2, "rtsp://u:p@10.0.0.5/s", True),
            (3, "rtsp://u:p@10.0.0.6/s", False), (4, "https://x/y.m3u8", True)]
    d = desired_paths(cams, record=True)
    assert d == {"cam1": {"record": True},
                 "aiv2": {"record": True, "source": "rtsp://u:p@10.0.0.5/s", "sourceOnDemand": False, "rtspTransport": "tcp"}}
    assert desired_paths(cams, record=False)["cam1"] == {"record": False}
    assert worker_url(2, "rtsp://u:p@10.0.0.5/s") == "rtsp://u:p@10.0.0.5/s"  # MediaMTX off: direct
    monkeypatch.setattr(recording.mediamtx, "enabled", True)
    assert worker_url(2, "rtsp://u:p@10.0.0.5/s") == "rtsp://127.0.0.1:8554/aiv2"  # one connection to the camera
    assert worker_url(1, "rtsp://127.0.0.1:8554/cam1") == "rtsp://127.0.0.1:8554/cam1"


class FakeMediaMTX:
    def __init__(self, existing):
        self.paths, self.calls = dict(existing), []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path))
        name = request.url.path.rsplit("/", 1)[-1]
        if request.url.path == "/v3/config/paths/list":
            return httpx.Response(200, json={"items": [{"name": n, **c} for n, c in self.paths.items()]})
        if "/add/" in request.url.path or "/patch/" in request.url.path and "pathdefaults" not in request.url.path:
            self.paths[name] = {**self.paths.get(name, {}), **__import__("json").loads(request.content)}
        if "/delete/" in request.url.path:
            self.paths.pop(name)
        return httpx.Response(200, json={"status": "ok"})


def test_sync_adds_changes_and_removes_only_our_paths(monkeypatch):
    fake = FakeMediaMTX({"all_others": {"record": False}, "cam1": {"record": False}, "aiv9": {"record": True}})
    m = recording.MediaMTX()
    m.enabled = True
    m._api = httpx.Client(base_url="http://mtx", transport=httpx.MockTransport(fake))
    result = m.sync({"cam1": {"record": True}, "aiv2": {"record": True, "source": "rtsp://x"}}, record_days=5)
    assert result == {"added": 1, "changed": 1, "removed": 1}
    assert set(fake.paths) == {"all_others", "cam1", "aiv2"}  # the catch-all entry is never touched
    assert fake.paths["cam1"]["record"] is True
    assert ("PATCH", "/v3/config/pathdefaults/patch") in fake.calls
    assert m.sync({"cam1": {"record": True}, "aiv2": {"record": True, "source": "rtsp://x"}}, 5) == \
        {"added": 0, "changed": 0, "removed": 0}  # already in sync: no writes


def test_size_cap_deletes_oldest_but_never_the_segment_being_written(tmp_path):
    for cam in ("cam1", "aiv2"):
        (tmp_path / cam).mkdir()
        for i in range(4):
            f = tmp_path / cam / f"2026-10-22_10-{i:02d}-00-000000.mp4"
            f.write_bytes(b"x" * 1000)
            os.utime(f, (time.time() - 1000 + i * 10,) * 2)
    out = enforce_cap(tmp_path, max_bytes=5000, min_free_bytes=0)
    left = sorted(p.name for p in tmp_path.rglob("*.mp4"))
    assert out["deleted"] == 3 and out["bytes"] == 5000
    assert "2026-10-22_10-03-00-000000.mp4" in left  # newest of each camera kept
    assert enforce_cap(tmp_path, max_bytes=0, min_free_bytes=0)["bytes"] == 2000  # never below one per camera


def test_recording_endpoints(client, camera, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    segs = [{"start": (now - timedelta(minutes=30)).isoformat(), "duration": 600.0},
            {"start": (now - timedelta(minutes=20)).isoformat(), "duration": 300.0},  # contiguous: merged
            {"start": (now - timedelta(days=2)).isoformat(), "duration": 600.0}]       # outside the window
    monkeypatch.setattr(recording.mediamtx, "list_recordings", lambda path: segs)
    r = client.get(f"/api/cameras/{camera['id']}/recordings",
                   params={"start": (now - timedelta(hours=1)).isoformat(), "end": now.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == f"aiv{camera['id']}" and len(body["segments"]) == 1
    seg = body["segments"][0]
    assert datetime.fromisoformat(seg["end"]) - datetime.fromisoformat(seg["start"]) == timedelta(minutes=15)
    assert client.get(f"/api/cameras/{camera['id']}/playback", params={"start": now.isoformat(), "duration": 9999}).status_code == 422


def test_settings_and_system_health(client):
    s = client.get("/api/settings").json()
    assert (s["recording_enabled"], s["recording_days"], s["recording_max_gb"]) == (True, 7, 20)
    assert client.patch("/api/settings", json={"recording_days": 0}).status_code == 422
    assert "recordings_bytes" in client.get("/api/system").json()


def test_deleting_a_camera_deletes_its_recordings(client, camera):
    from app.config import settings
    d = settings.recordings_path / f"aiv{camera['id']}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-10-22_10-00-00-000000.mp4").write_bytes(b"x")
    assert client.delete(f"/api/cameras/{camera['id']}").status_code == 204
    assert not d.exists()


def test_shared_phone_path_recordings_survive_until_the_last_camera_goes(client):
    from app.config import settings
    ids = [client.post("/api/cameras", json={"name": n, "stream_url": "rtsp://127.0.0.1:8554/cam7"}).json()["id"] for n in ("A", "B")]
    d = settings.recordings_path / "cam7"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-10-22_10-00-00-000000.mp4").write_bytes(b"x")
    client.delete(f"/api/cameras/{ids[0]}")
    assert d.exists()  # camera B still records here
    client.delete(f"/api/cameras/{ids[1]}")
    assert not d.exists()
