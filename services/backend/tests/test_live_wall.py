import base64
import time

from tests.conftest import WORKER

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def live_frame(camera_id, preview=None):
    return {"camera_id": camera_id, "at": time.time(), "width": 640, "height": 480,
            "objects": [{"box": [0.1, 0.2, 0.3, 0.9], "cls": "person", "track": 7, "conf": 0.91}],
            "preview": base64.b64encode(preview).decode() if preview else None}


def live_cameras(client):
    return client.get("/api/internal/config", headers=WORKER).json()["live_cameras"]


def test_watching_drives_worker_and_pushes_detections(client, camera):
    cam = camera["id"]
    assert live_cameras(client) == []
    with client.websocket_connect("/api/ws") as ws:
        ws.send_json({"type": "watch", "cameras": [cam]})
        ws.send_text("ping")  # round trip so the watch message has been handled
        deadline = time.time() + 5
        while live_cameras(client) != [cam] and time.time() < deadline:
            time.sleep(0.05)
        assert live_cameras(client) == [cam]

        assert client.post("/api/internal/live", headers=WORKER, json=live_frame(cam, JPEG)).status_code == 204
        msg = ws.receive_json()
        assert msg["type"] == "detections" and msg["data"]["camera_id"] == cam and msg["data"]["preview"] is True
        assert msg["data"]["objects"][0]["track"] == 7

        r = client.get(f"/api/cameras/{cam}/preview")
        assert r.status_code == 200 and r.content == JPEG and r.headers["cache-control"] == "no-store"

        ws.send_json({"type": "watch", "cameras": []})
        ws.send_text("ping")
        deadline = time.time() + 5
        while live_cameras(client) and time.time() < deadline:
            time.sleep(0.05)
    assert live_cameras(client) == []  # nobody watching: the worker stops sending


def test_live_ingest_and_preview_auth(client, anon, camera):
    cam = camera["id"]
    assert client.post("/api/internal/live", json=live_frame(cam)).status_code == 401  # worker only
    assert client.post("/api/internal/live", headers=WORKER,
                       json={**live_frame(cam), "preview": "not base64!"}).status_code == 422
    assert client.post("/api/internal/live", headers=WORKER,
                       json={**live_frame(cam), "objects": [{"box": [0, 0, 1], "cls": "x", "conf": 1}]}).status_code == 422
    client.post("/api/internal/live", headers=WORKER, json=live_frame(cam, JPEG))
    assert anon.get(f"/api/cameras/{cam}/preview").status_code == 401
    assert client.get("/api/cameras/99999/preview").status_code == 404


def test_old_preview_expires(client, camera):
    from app import live
    live.live_previews[camera["id"]] = (JPEG, time.time() - 60)
    assert client.get(f"/api/cameras/{camera['id']}/preview").status_code == 404
