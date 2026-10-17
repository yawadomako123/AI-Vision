"""Sightings: one compact record per tracked person/vehicle per camera, for search.

While a track is visible we keep only: its best cut-out (largest x most confident, refreshed at
most once a second), a sparse path of where it walked, and first/last seen times. When the
track is lost (or after `max_seconds`), the sighting is finished: the cut-out is turned into a
SigLIP embedding (the "meaning fingerprint" used for plain-English search), and a small context
image with the path drawn on it is made. All of that is ~25 KB per sighting; no video is kept.
"""

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

log = logging.getLogger(__name__)


class SiglipEmbedder:
    """SigLIP image tower (ONNX) on OpenVINO (Intel GPU/CPU) with an ONNX Runtime fallback."""

    def __init__(self, model_dir: Path, device: str = "GPU"):
        meta = json.loads((model_dir / "siglip.json").read_text(encoding="utf-8"))
        self.size = meta["image_size"]
        self.mean = np.array(meta["mean"], np.float32)
        self.std = np.array(meta["std"], np.float32)
        path = model_dir / "vision.onnx"
        try:
            import openvino as ov

            core = ov.Core()
            core.set_property({"CACHE_DIR": str(model_dir / ".ov_cache")})
            compiled = core.compile_model(core.read_model(path), device)
            request = compiled.create_infer_request()
            self._run = lambda x: request.infer({0: x})[compiled.output(0)]
            self.provider = f"OpenVINO {device}"
        except Exception as exc:  # noqa: BLE001
            import onnxruntime as ort

            log.warning("SigLIP on OpenVINO %s unavailable (%s); using ONNX Runtime CPU", device, exc)
            session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            self._run = lambda x: session.run(None, {"pixel_values": x})[0]
            self.provider = "ONNX Runtime CPU"

    def __call__(self, crop_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(cv2.resize(crop_bgr, (self.size, self.size), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB)
        x = ((rgb.astype(np.float32) / 255.0 - self.mean) / self.std).transpose(2, 0, 1)[None]
        emb = np.asarray(self._run(x), np.float32)[0]
        return emb / (np.linalg.norm(emb) + 1e-12)


@dataclass
class _Track:
    track_id: int
    object_class: str
    first_seen: float
    last_seen: float
    best_score: float = 0.0
    best_crop: np.ndarray | None = None
    best_frame: np.ndarray | None = None
    best_box: tuple[int, int, int, int] | None = None
    best_checked: float = 0.0
    max_confidence: float = 0.0
    detections: int = 0
    path: list[list[float]] = field(default_factory=list)
    path_at: float = 0.0


def _jpeg(img: np.ndarray, quality: int = 80) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode() if ok else ""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class SightingCollector:
    def __init__(self, camera_id: int, send, embedder: SiglipEmbedder | None,
                 lost_seconds: float = 4.0, max_seconds: float = 600.0, min_detections: int = 3):
        self.camera_id = camera_id
        self.send = send  # callable(payload: dict)
        self.embedder = embedder
        self.lost_seconds, self.max_seconds, self.min_detections = lost_seconds, max_seconds, min_detections
        self._tracks: dict[int, _Track] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"sightings-{camera_id}")

    def update(self, detections: sv.Detections, names: list[str], positions: np.ndarray,
               frame: np.ndarray, ts: float) -> None:
        if self.embedder is None or detections.tracker_id is None:
            return
        h, w = frame.shape[:2]
        for i, tid in enumerate(detections.tracker_id):
            tid = int(tid)
            t = self._tracks.get(tid)
            if t is None:
                t = self._tracks[tid] = _Track(tid, names[i], ts, ts)
            t.last_seen = ts
            t.detections += 1
            conf = float(detections.confidence[i])
            t.max_confidence = max(t.max_confidence, conf)
            if ts - t.path_at >= 1.0 and len(t.path) < 60:
                x, y = positions[i]
                t.path.append([round(float(x) / w, 4), round(float(y) / h, 4)])
                t.path_at = ts
            x1, y1, x2, y2 = (int(v) for v in detections.xyxy[i])
            score = max(0, x2 - x1) * max(0, y2 - y1) * conf
            if score > t.best_score and ts - t.best_checked >= 1.0:
                t.best_checked = ts
                pad_x, pad_y = int((x2 - x1) * 0.08), int((y2 - y1) * 0.08)
                crop = frame[max(0, y1 - pad_y):min(h, y2 + pad_y), max(0, x1 - pad_x):min(w, x2 + pad_x)]
                if crop.size:
                    t.best_score, t.best_crop, t.best_frame, t.best_box = score, crop.copy(), frame, (x1, y1, x2, y2)
        for tid in [tid for tid, t in self._tracks.items()
                    if ts - t.last_seen > self.lost_seconds or ts - t.first_seen > self.max_seconds]:
            self._finish(self._tracks.pop(tid))

    def tick(self, now: float) -> None:
        """Finish tracks that stopped updating because no frames arrive (camera offline)."""
        for tid in [tid for tid, t in self._tracks.items() if now - t.last_seen > self.lost_seconds + 2]:
            self._finish(self._tracks.pop(tid))

    def flush(self) -> None:
        for t in self._tracks.values():
            self._finish(t)
        self._tracks.clear()

    def _finish(self, t: _Track) -> None:
        if t.detections < self.min_detections or t.best_crop is None:
            return  # a flicker, not a sighting
        self._pool.submit(self._process, t)

    def _process(self, t: _Track) -> None:
        try:
            emb = self.embedder(t.best_crop)
            thumb = t.best_crop
            if thumb.shape[0] > 256:
                s = 256 / thumb.shape[0]
                thumb = cv2.resize(thumb, (max(1, int(thumb.shape[1] * s)), 256), interpolation=cv2.INTER_AREA)
            ctx = t.best_frame.copy()
            fh, fw = ctx.shape[:2]
            if t.path:
                pts = np.array([[x * fw, y * fh] for x, y in t.path], np.int32)
                cv2.polylines(ctx, [pts], False, (0, 255, 255), 3, cv2.LINE_AA)
                cv2.circle(ctx, tuple(pts[0]), 6, (0, 200, 0), -1)
                cv2.circle(ctx, tuple(pts[-1]), 6, (0, 0, 255), -1)
            x1, y1, x2, y2 = t.best_box
            cv2.rectangle(ctx, (x1, y1), (x2, y2), (255, 160, 0), 2)
            s = 360 / fw
            ctx = cv2.resize(ctx, (360, int(fh * s)), interpolation=cv2.INTER_AREA)
            self.send({
                "camera_id": self.camera_id, "track_id": t.track_id, "object_class": t.object_class,
                "first_seen": _iso(t.first_seen), "last_seen": _iso(t.last_seen),
                "confidence": round(t.max_confidence, 3), "path": t.path,
                "embedding": base64.b64encode(emb.astype(np.float16).tobytes()).decode(),
                "thumbnail": _jpeg(thumb), "context": _jpeg(ctx, 70),
            })
        except Exception:  # noqa: BLE001 - search is a nice-to-have; never break detection
            log.exception("could not process sighting for track %s", t.track_id)
