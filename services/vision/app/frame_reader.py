"""Camera ingest: one background thread per source that always holds only the newest frame.

Stale frames are overwritten, never queued, so a slow detector cannot build up latency.
Network sources reconnect with exponential backoff. HTTP(S) sources (HLS webcams) deliver video
in multi-second bursts, so like files they are paced to real time by their timestamps.
"""

import logging
import threading
import time
from dataclasses import dataclass

import av
import numpy as np

from app.privacy import PrivacyMask

log = logging.getLogger(__name__)


@dataclass
class Frame:
    image: np.ndarray  # BGR, HxWx3
    captured_at: float  # time.time()
    index: int


class FrameReader:
    def __init__(self, camera_id: str, source: str, max_backoff: float = 10.0):
        self.camera_id = camera_id
        self.source = source
        self.is_file = not source.lower().startswith(("rtsp://", "rtsps://", "http://", "https://"))
        self.is_http = source.lower().startswith(("http://", "https://"))
        self.max_backoff = max_backoff

        self.status = "connecting"  # connecting | online | offline
        self.last_frame_at: float | None = None
        self.reconnects = 0
        self.frames_decoded = 0

        # Optional callback for every decoded frame (image, unix time), e.g. the clip pre-roll.
        # Called on the reader thread; it must be fast and thread-safe.
        self.on_frame = None
        # Privacy masks are applied here, first, so nothing downstream sees the masked areas.
        self.mask = PrivacyMask()
        self._latest: Frame | None = None
        self._lock = threading.Lock()
        self._new_frame = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"reader-{camera_id}", daemon=True)

    def start(self) -> "FrameReader":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def latest(self, timeout: float = 1.0) -> Frame | None:
        """Return the newest frame not yet returned, or None if none arrived within timeout."""
        if not self._new_frame.wait(timeout):
            return None
        with self._lock:
            self._new_frame.clear()
            return self._latest

    def _open(self):
        if self.is_file:
            return av.open(self.source)
        if self.is_http:
            return av.open(
                self.source,
                options={"reconnect": "1", "reconnect_streamed": "1", "reconnect_delay_max": "5",
                         # HLS: start ~6 segments behind live. Many feeds update their playlist
                         # in bursts (every 10-20 s); the cushion keeps frames flowing in between,
                         # at the cost of HLS running ~30 s behind real time (RTSP is unaffected).
                         "live_start_index": "-6"},
                # Segments arrive every few seconds: a short read timeout would cut the stream.
                timeout=(10.0, 20.0),
            )
        return av.open(
            self.source,
            options={
                "rtsp_transport": "tcp",
                "timeout": "5000000",  # socket timeout, microseconds
                "fflags": "nobuffer",
                "flags": "low_delay",
            },
            timeout=(5.0, 5.0),  # (open, read) seconds: a dead camera cannot hang us
        )

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with self._open() as container:
                    stream = container.streams.video[0]
                    stream.thread_type = "AUTO"
                    # Pace files and bursty HTTP streams to real time so they behave like cameras.
                    paced = self.is_file or self.is_http
                    fallback_fps = float(stream.average_rate or 15)
                    anchor: tuple[float, float] | None = None  # (wall clock, stream time) of the pacing start

                    for n, frame in enumerate(container.decode(stream)):
                        if self._stop.is_set():
                            return
                        if paced:
                            t = frame.time if frame.time is not None else n / fallback_fps
                            if anchor is None:
                                anchor = (time.monotonic(), t)
                            delay = anchor[0] + (t - anchor[1]) - time.monotonic()
                            if delay > 0:
                                self._stop.wait(min(delay, 2.0))
                            elif delay < -2.0:  # fell behind (network stall): restart pacing here
                                anchor = (time.monotonic(), t)
                        self._publish(frame.to_ndarray(format="bgr24"))
                        if self.status != "online":
                            log.info("[%s] online (%sx%s)", self.camera_id, frame.width, frame.height)
                            self.status = "online"
                            backoff = 1.0

                if self.is_file:
                    log.info("[%s] end of file, looping", self.camera_id)
                    continue
                raise ConnectionError("stream ended")
            except Exception as exc:  # noqa: BLE001 - any ingest failure means reconnect
                if self._stop.is_set():
                    return
                if self.status != "offline":
                    # FFmpeg errors echo the URL, which can contain the camera password.
                    log.warning("[%s] offline: %s", self.camera_id, str(exc).replace(self.source, "<stream>"))
                self.status = "offline"
                self.reconnects += 1
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.max_backoff)

    def _publish(self, image: np.ndarray) -> None:
        now = time.time()
        self.mask.apply(image)
        with self._lock:
            self.frames_decoded += 1
            self._latest = Frame(image=image, captured_at=now, index=self.frames_decoded)
            self.last_frame_at = now
        self._new_frame.set()
        if self.on_frame is not None:
            try:
                self.on_frame(image, now)
            except Exception:  # noqa: BLE001 - never let a consumer break ingest
                log.exception("[%s] frame callback failed", self.camera_id)
