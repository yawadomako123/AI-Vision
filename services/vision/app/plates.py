"""Number-plate reading for vehicle sightings (fast-alpr: plate finder + OCR, MIT licence, ONNX).

Runs once per vehicle, when its sighting finishes, on the few best pictures kept while it was in
view (largest and clearest). Single reads often get a character wrong ("BROJY19" vs "BRDJ119"),
so the reads are combined by a confidence-weighted vote per character position. The plate
image is always kept with the text: the reading is an aid, the picture is the evidence.

Accuracy depends mostly on the camera: the plate should be at least ~130 px wide in the picture
(a camera aimed at a gate or entrance, high-resolution stream, fast shutter, IR at night).
Models are downloaded once (~10 MB) on first use. About 140 ms per picture on a laptop CPU.

False "plates": a live test read the stream's on-screen text and a street sign that overlapped
passing cars. So a candidate must look like a plate on a vehicle (lower part, not too big,
plate-like shape, plate finder fairly sure), and text that keeps turning up on different
vehicles of one camera is treated as a fixed sign (see StaticText).
"""

import logging
import re
import threading
from collections import Counter, defaultdict

import numpy as np

log = logging.getLogger(__name__)

MIN_CHARS = 4
MIN_DETECTION = 0.5
# Measured on real plates seen from behind/at an angle: centre 0.55-0.92 down the vehicle,
# 13-20% of its width, 1.7-2.4 wide:tall. Limits are looser to allow other angles and plate
# formats (long plates ~4.7:1, square/motorcycle ~1.6:1).
MIN_CENTRE_Y, MAX_WIDTH, MAX_HEIGHT, MIN_ASPECT, MAX_ASPECT = 0.35, 0.45, 0.3, 1.2, 6.0
MIN_CONFIDENCE = 0.5
DETECTOR = "yolo-v9-t-384-license-plate-end2end"
OCR = "cct-s-v2-global-model"


def normalize(text: str) -> str:
    """Upper-case letters and digits only: 'gr 1234-23' -> 'GR123423'."""
    return re.sub(r"[^0-9A-Z]", "", (text or "").upper())


def repetitive(text: str) -> bool:
    """'L4444444', '1111111': a row of lights or a pattern, not a plate (plates rarely repeat a
    character more than a few times)."""
    return len(text) >= 5 and Counter(text).most_common(1)[0][1] / len(text) >= 0.6


def vote(reads: list[tuple[str, float]]) -> tuple[str, float]:
    """Combine several reads of the same plate.

    The most supported length wins (weighted by confidence); then each position takes the
    character with the most confidence behind it. Returns (text, confidence 0..1), where the
    confidence reflects both the OCR's own confidence and how much the reads agree.
    """
    reads = [(normalize(t), c) for t, c in reads if normalize(t)]
    if not reads:
        return "", 0.0
    by_len: dict[int, float] = defaultdict(float)
    for t, c in reads:
        by_len[len(t)] += c
    length = max(by_len, key=by_len.get)
    same = [(t, c) for t, c in reads if len(t) == length]
    text, agreement = [], []
    for i in range(length):
        weights: Counter = Counter()
        for t, c in same:
            weights[t[i]] += c
        ch, w = weights.most_common(1)[0]
        text.append(ch)
        agreement.append(w / sum(weights.values()))
    mean_conf = float(np.mean([c for _, c in same]))
    support = by_len[length] / sum(by_len.values())
    return "".join(text), round(mean_conf * float(np.mean(agreement)) * (0.5 + 0.5 * support), 3)


def _iou(a, b) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _signature(img: np.ndarray | None) -> np.ndarray | None:
    """Small normalised greyscale version of a plate picture, for comparing looks."""
    if img is None or not img.size:
        return None
    import cv2

    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img, (64, 20),
                   interpolation=cv2.INTER_AREA).astype(np.float32)
    g -= g.mean()
    n = np.linalg.norm(g)
    return g / n if n > 0 else None


class StaticText:
    """Spots fixed signs and on-screen overlays that the plate finder mistakes for plates.

    Something found on 3+ different vehicles of one camera within `window` seconds is a sign,
    not a plate, when either (a) its reading is nearly the same text each time, or (b) it sits
    in the same spot of the picture AND looks nearly identical (OCR on a sign gives different
    random text each time, so its looks and place are the reliable clue). Real plates of cars
    stopping at the same barrier share the spot but look different, so they're kept.
    """

    def __init__(self, window: float = 1800.0, other_tracks: int = 2, alike: float = 0.75,
                 same_spot: float = 0.4, same_look: float = 0.85):
        self.window, self.other_tracks, self.alike = window, other_tracks, alike
        self.same_spot, self.same_look = same_spot, same_look
        self._seen: list[tuple[float, int, str, tuple | None, np.ndarray | None]] = []

    def is_static(self, text: str, track_id: int, now: float, frame_box: tuple | None = None,
                  image: np.ndarray | None = None) -> bool:
        import difflib

        sig = _signature(image)
        self._seen = [s for s in self._seen if now - s[0] <= self.window]
        others = set()
        for _, t, txt, box, osig in self._seen:
            if t == track_id:
                continue
            same_text = difflib.SequenceMatcher(None, txt, text).ratio() >= self.alike
            same_place_and_look = (box is not None and frame_box is not None and sig is not None and osig is not None
                                   and _iou(box, frame_box) >= self.same_spot
                                   and float((sig * osig).sum()) >= self.same_look)
            if same_text or same_place_and_look:
                others.add(t)
        self._seen.append((now, track_id, text, frame_box, sig))
        return len(others) >= self.other_tracks


class PlateReader:
    def __init__(self) -> None:
        self._alpr = None
        self._lock = threading.Lock()
        self.available = True

    def _load(self):
        if self._alpr is None:
            from fast_alpr import ALPR  # imported lazily: only when plate reading is switched on

            self._alpr = ALPR(detector_model=DETECTOR, ocr_model=OCR, ocr_device="cpu")
            log.info("Plate reader ready (%s + %s)", DETECTOR, OCR)
        return self._alpr

    @staticmethod
    def plausible(box, crop_w: int, crop_h: int, det_conf: float) -> bool:
        """Does this detection look like a number plate on this vehicle?"""
        w, h = box.x2 - box.x1, box.y2 - box.y1
        if det_conf < MIN_DETECTION or w <= 0 or h <= 0:
            return False
        cy = (box.y1 + box.y2) / 2 / crop_h
        return (cy >= MIN_CENTRE_Y and w / crop_w <= MAX_WIDTH and h / crop_h <= MAX_HEIGHT
                and MIN_ASPECT <= w / h <= MAX_ASPECT)

    def read(self, crop_bgr: np.ndarray) -> tuple[str, float, np.ndarray | None]:
        """One picture -> (text, confidence, plate image) for the most confident plausible plate."""
        text, conf, img, _ = self.read_with_box(crop_bgr)
        return text, conf, img

    def read_with_box(self, crop_bgr: np.ndarray):
        """Like read(), plus the plate's box in the crop's pixels (or None)."""
        with self._lock:
            try:
                results = self._load().predict(crop_bgr)
            except Exception:  # noqa: BLE001 - e.g. no internet for the first model download
                log.exception("plate reading failed")
                self.available = False
                return "", 0.0, None, None
        h, w = crop_bgr.shape[:2]
        candidates = [r for r in results if self.plausible(r.detection.bounding_box, w, h, r.detection.confidence)]
        best = max(candidates, key=lambda r: r.detection.confidence, default=None)
        if best is None or best.ocr is None:
            return "", 0.0, None, None
        conf = best.ocr.confidence
        conf = float(np.mean(conf)) if isinstance(conf, (list, tuple, np.ndarray)) else float(conf or 0)
        b = best.detection.bounding_box
        pad = 4
        plate = crop_bgr[max(0, b.y1 - pad):min(h, b.y2 + pad), max(0, b.x1 - pad):min(w, b.x2 + pad)]
        return best.ocr.text or "", conf, (plate.copy() if plate.size else None), (b.x1, b.y1, b.x2, b.y2)

    def read_many(self, crops: list, frame_boxes: list | None = None):
        """Several pictures of one vehicle -> voted (text, confidence, best plate image, plate box in
        the frame normalised 0..1 or None), or ("", 0, None, None).
        frame_boxes: for each crop, (x1, y1, frame_width, frame_height) of the crop in its frame."""
        reads, best_img, best_conf, best_box = [], None, -1.0, None
        for i, crop in enumerate(crops):
            text, conf, img, box = self.read_with_box(crop)
            if text:
                reads.append((text, conf))
                if img is not None and conf > best_conf:
                    best_img, best_conf = img, conf
                    if box is not None and frame_boxes and i < len(frame_boxes):
                        ox, oy, fw, fh = frame_boxes[i]
                        best_box = ((ox + box[0]) / fw, (oy + box[1]) / fh, (ox + box[2]) / fw, (oy + box[3]) / fh)
        text, conf = vote(reads)
        if len(text) < MIN_CHARS or conf < MIN_CONFIDENCE or repetitive(text):
            return "", 0.0, None, None
        return text, conf, best_img, best_box
