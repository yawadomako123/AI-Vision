"""Vision worker <-> API: fetch cameras and rules, report status, deliver events and counts.

Events and count samples go through a background queue with retries, so a restarting API
never blocks inference and data survives short outages (up to `max_queue` items, oldest
dropped first).
"""

import logging
import queue
import threading
import time

import httpx

log = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, base_url: str, token: str, max_queue: int = 1000):
        self.http = httpx.Client(base_url=base_url, headers={"X-Service-Token": token}, timeout=5.0)
        self._outbox: queue.Queue[tuple[str, object, str]] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._sender = threading.Thread(target=self._send_loop, name="api-sender", daemon=True)
        self._sender.start()

    def cameras(self) -> list[dict]:
        r = self.http.get("/api/internal/cameras")
        r.raise_for_status()
        return r.json()

    def rules(self) -> dict[int, list[dict]]:
        r = self.http.get("/api/internal/rules")
        r.raise_for_status()
        return {int(cam): rules for cam, rules in r.json().items()}

    def report_status(self, camera_id: int, status: str, last_frame_at: str | None, infer_fps: float | None) -> None:
        try:
            self.http.post(f"/api/internal/cameras/{camera_id}/status",
                           json={"status": status, "last_frame_at": last_frame_at, "infer_fps": infer_fps})
        except httpx.HTTPError as exc:
            log.debug("status report failed: %s", exc)

    def send_event(self, event: dict) -> None:
        self._enqueue("/api/internal/events", event, event["id"])

    def send_counts(self, samples: list[dict]) -> None:
        if samples:
            self._enqueue("/api/internal/counts", samples, f"counts@{samples[0]['bucket_start']}")

    def _enqueue(self, path: str, payload: object, label: str) -> None:
        while True:
            try:
                self._outbox.put_nowait((path, payload, label))
                return
            except queue.Full:
                _, _, dropped = self._outbox.get_nowait()
                log.warning("API outbox full, dropped oldest item %s", dropped)

    def close(self, timeout: float = 5.0) -> None:
        """Give queued items a moment to flush, then stop."""
        deadline = time.monotonic() + timeout
        while not self._outbox.empty() and time.monotonic() < deadline:
            time.sleep(0.1)
        self._stop.set()
        self._sender.join(timeout=2)
        self.http.close()

    def _send_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                path, payload, label = self._outbox.get(timeout=0.5)
            except queue.Empty:
                continue
            while not self._stop.is_set():
                try:
                    r = self.http.post(path, json=payload)
                    if r.status_code < 500:
                        if r.status_code >= 400:  # bad payload / deleted camera: retrying won't help
                            log.warning("API rejected %s: %s %s", label, r.status_code, r.text[:200])
                        backoff = 1.0
                        break
                    raise httpx.HTTPStatusError("server error", request=r.request, response=r)
                except httpx.HTTPError as exc:
                    log.warning("API unreachable (%s); retrying %s in %.0fs", exc, label, backoff)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 30)
