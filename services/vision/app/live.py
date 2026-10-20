"""Live wall feed: detection boxes (and a small preview image about once a second) for the
cameras someone is watching in the dashboard.

Deliberately lossy: only the newest frame per camera is kept and sent from a background
thread. If the API is slow or down, old frames are dropped instead of queued, so the live
view can never slow detection or build up a backlog. Nothing is sent while nobody watches.
"""

import base64
import logging
import threading
import time

import cv2
import httpx
import numpy as np
import supervision as sv

log = logging.getLogger(__name__)

PREVIEW_EVERY = 1.0  # seconds between preview images per camera
PREVIEW_WIDTH = 640
SEND_INTERVAL = 0.2


def live_payload(camera_id: int, detections: sv.Detections, names: list[str], width: int, height: int,
                 at: float, image: np.ndarray | None = None) -> dict:
    objects = []
    for i, (x1, y1, x2, y2) in enumerate(detections.xyxy):
        objects.append({
            "box": [round(float(x1) / width, 4), round(float(y1) / height, 4),
                    round(float(x2) / width, 4), round(float(y2) / height, 4)],
            "cls": names[i],
            "track": int(detections.tracker_id[i]) if detections.tracker_id is not None else None,
            "conf": round(float(detections.confidence[i]), 3),
        })
    payload = {"camera_id": camera_id, "at": at, "width": width, "height": height, "objects": objects, "preview": None}
    if image is not None:
        if width > PREVIEW_WIDTH:
            image = cv2.resize(image, (PREVIEW_WIDTH, round(height * PREVIEW_WIDTH / width)), interpolation=cv2.INTER_AREA)
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            payload["preview"] = base64.b64encode(jpeg.tobytes()).decode()
    return payload


class LivePublisher:
    def __init__(self, base_url: str, token: str):
        self._http = httpx.Client(base_url=base_url, headers={"X-Service-Token": token}, timeout=2.0)
        self._pending: dict[int, dict] = {}
        self._last_preview: dict[int, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="live-sender", daemon=True)
        self._thread.start()

    def wants_preview(self, camera_id: int, now: float) -> bool:
        if now - self._last_preview.get(camera_id, 0) >= PREVIEW_EVERY:
            self._last_preview[camera_id] = now
            return True
        return False

    def offer(self, payload: dict) -> None:
        with self._lock:
            old = self._pending.get(payload["camera_id"])
            if old and old.get("preview") and not payload.get("preview"):
                payload["preview"] = old["preview"]  # don't lose an unsent preview to a newer box update
            self._pending[payload["camera_id"]] = payload

    def _run(self) -> None:
        while not self._stop.wait(SEND_INTERVAL):
            with self._lock:
                batch, self._pending = list(self._pending.values()), {}
            for payload in batch:
                try:
                    self._http.post("/api/internal/live", json=payload).raise_for_status()
                except httpx.HTTPError as exc:
                    log.debug("live frame for cam%s dropped: %s", payload["camera_id"], exc)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._http.close()
