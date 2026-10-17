"""Short evidence clips around alerts, kept deliberately small.

Each camera keeps a few seconds of downscaled, low-fps frames in memory (pre-roll). When a
rule fires, the recorder takes that pre-roll, keeps collecting for `post_seconds`, then encodes
an H.264 MP4 in a background thread. Nothing is recorded continuously, and alerts that fire
while a clip is still recording share that clip instead of starting another.

Typical clip: 8 s at 6 fps, <=640 px wide, CRF 32 -> roughly 100-300 KB.
"""

import logging
import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np

log = logging.getLogger(__name__)
_encoder = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-encoder")


@dataclass
class ClipConfig:
    enabled: bool = True
    pre_seconds: float = 3.0
    post_seconds: float = 5.0
    fps: float = 6.0
    max_width: int = 640
    crf: int = 32


def _small(frame: np.ndarray, max_width: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w > max_width:
        scale = max_width / w
        frame = cv2.resize(frame, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)
    h, w = frame.shape[:2]
    return frame[: h - h % 2, : w - w % 2]  # H.264 needs even dimensions


def encode_clip(frames: list[tuple[float, np.ndarray]], path: Path, fps: float, crf: int) -> None:
    """Write (timestamp, frame) pairs to an MP4 atomically (tmp file + rename, so it's never served
    half-written). Frames keep their real capture times, so playback runs at real speed even if
    the camera or a busy machine delivered fewer frames than `fps`."""
    if not frames:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part.mp4")
    h, w = frames[0][1].shape[:2]
    t0 = frames[0][0]
    ms = Fraction(1, 1000)
    with av.open(str(tmp), "w", format="mp4", options={"movflags": "+faststart"}) as out:
        stream = out.add_stream("libx264", rate=round(fps))
        stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
        stream.codec_context.time_base = ms
        stream.options = {"crf": str(crf), "preset": "veryfast", "tune": "zerolatency"}
        last_pts = -1
        for ts, f in frames:
            if f.shape[:2] != (h, w):
                f = cv2.resize(f, (w, h))
            vf = av.VideoFrame.from_ndarray(f, format="bgr24")
            vf.pts = max(last_pts + 1, int(round((ts - t0) * 1000)))  # strictly increasing, in ms
            vf.time_base = ms
            last_pts = vf.pts
            for packet in stream.encode(vf):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    os.replace(tmp, path)


class ClipRecorder:
    """Per-camera pre-roll buffer + at most one clip being recorded at a time. Thread-safe:
    frames arrive from the reader thread, start() is called from the main loop."""

    def __init__(self, config: ClipConfig | None = None):
        self.config = config or ClipConfig()
        self._lock = threading.Lock()
        self._buffer: deque[tuple[float, np.ndarray]] = deque()
        self._last_kept = 0.0
        self._active: dict | None = None  # {"path", "until", "frames"}

    def configure(self, config: ClipConfig) -> None:
        with self._lock:
            self.config = config
            if not config.enabled:
                self._buffer.clear()

    def add_frame(self, frame: np.ndarray, ts: float) -> None:
        cfg = self.config
        if not cfg.enabled or ts - self._last_kept < 1.0 / cfg.fps:
            return
        self._last_kept = ts
        small = _small(frame, cfg.max_width)
        finished = None
        with self._lock:
            self._buffer.append((ts, small))
            while self._buffer and ts - self._buffer[0][0] > cfg.pre_seconds:
                self._buffer.popleft()
            if self._active is not None:
                self._active["frames"].append((ts, small))
                if ts >= self._active["until"]:
                    finished, self._active = self._active, None
        if finished:
            _encoder.submit(self._encode, finished)

    def start(self, path: Path, ts: float) -> Path | None:
        """Begin (or join) a clip for an alert at time `ts`. Returns the clip path, or None if off."""
        cfg = self.config
        if not cfg.enabled:
            return None
        with self._lock:
            if self._active is not None:
                # Alert during an ongoing clip: share it, and keep recording a little longer
                # (bounded, so a busy zone can't produce one endless clip).
                limit = self._active["started"] + cfg.pre_seconds + 3 * cfg.post_seconds
                self._active["until"] = min(max(self._active["until"], ts + cfg.post_seconds), limit)
                return self._active["path"]
            self._active = {
                "path": path, "started": ts, "until": ts + cfg.post_seconds,
                "frames": list(self._buffer),
            }
            return path

    def tick(self, now: float) -> None:
        """Finish a clip whose frames stopped arriving (camera dropped mid-recording)."""
        with self._lock:
            stalled = self._active is not None and now > self._active["until"] + 2.0
        if stalled:
            self.flush()

    def flush(self) -> None:
        """Encode a clip that is still recording (e.g. on shutdown or stream loss)."""
        with self._lock:
            finished, self._active = self._active, None
        if finished:
            _encoder.submit(self._encode, finished)

    def _encode(self, clip: dict) -> None:
        try:
            encode_clip(clip["frames"], clip["path"], self.config.fps, self.config.crf)
            frames = clip["frames"]
            log.info("clip saved %s (%d frames over %.1f s, %.0f KB)", clip["path"].name, len(frames),
                     frames[-1][0] - frames[0][0] if frames else 0, clip["path"].stat().st_size / 1024)
        except Exception:  # noqa: BLE001 - a failed clip must never take down detection
            log.exception("could not encode clip %s", clip["path"])
