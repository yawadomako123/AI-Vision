"""Vehicle colour and body type from a sighting's SigLIP embedding (zero-shot, no extra model).

Each vehicle sighting already carries a SigLIP image embedding (for plain-English search). We
compare it with text embeddings of a few phrasings per colour and per body type, averaged per
label, and take the best match; SigLIP's own logit scale turns similarities into probabilities.
The detector's class settles the obvious cases (motorcycle, bicycle, bus) and narrows the rest.

Cheap: the text side is encoded once (about 60 short phrases), then each vehicle costs a couple
of small matrix products.

Poor light: at dusk and night, brake lights and headlight glare flood the picture and white cars
come out "red". Tested on 90 real crops (daylight and dusk traffic), colour and type are reported
as unknown when the cut-out has brake-light glow, or when SigLIP says "car lights at night" and
there is visible glare. That turned ~15 wrong dusk colours into "unknown" without losing
daylight results.
"""

import threading

import numpy as np

from app.search_engine import TextEncoder

COLORS = ["white", "black", "silver", "grey", "red", "blue", "green", "yellow", "orange", "brown"]
COLOR_TEMPLATES = ["a photo of a {} car", "a {} vehicle on the road", "a {} colored vehicle"]
COLOR_WORDS = {"brown": "brown or beige", "grey": "grey", "silver": "silver metallic"}

# Body types for car/truck detections. Label -> phrasings.
CAR_TYPES = {
    "saloon": ["a photo of a sedan car", "a saloon car"],
    "hatchback": ["a photo of a small hatchback car", "a hatchback"],
    "SUV": ["a photo of an SUV", "a sport utility vehicle"],
    "pickup": ["a photo of a pickup truck", "a pickup truck with an open cargo bed"],
    "van": ["a photo of a van", "a delivery van"],
    "minibus": ["a photo of a minibus", "a passenger minibus"],
    "truck": ["a photo of a lorry", "a large truck"],
}
FIXED_TYPES = {"motorcycle": "motorcycle", "bicycle": "bicycle", "bus": "bus"}
VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "vehicle"}

NIGHT_PHRASES = ["a blurry photo of car headlights glare at night", "a car at night with bright brake lights",
                 "a dark night photo of a car with lights on", "an overexposed blurry photo of car lights"]
MIN_COLOR_CONFIDENCE = 0.45
MIN_TYPE_CONFIDENCE = 0.35


class VehicleAttributes:
    def __init__(self, encoder: TextEncoder):
        self.encoder = encoder
        self._lock = threading.Lock()
        self._color_matrix: np.ndarray | None = None
        self._type_labels: list[str] = []
        self._type_matrix: np.ndarray | None = None
        self._night: np.ndarray | None = None
        self._scale = 117.33

    @property
    def available(self) -> bool:
        return self.encoder.available

    def _label_matrix(self, phrasings: list[list[str]]) -> np.ndarray:
        rows = []
        for group in phrasings:
            v = np.mean([self.encoder.encode(p) for p in group], axis=0)
            rows.append(v / (np.linalg.norm(v) + 1e-12))
        return np.stack(rows)

    def _load(self) -> None:
        with self._lock:
            if self._color_matrix is not None:
                return
            self._scale = float(self.encoder.meta.get("logit_scale", self._scale)) if self.encoder.meta else self._scale
            self._color_matrix = self._label_matrix(
                [[t.format(COLOR_WORDS.get(c, c)) for t in COLOR_TEMPLATES] for c in COLORS])
            self._type_labels = list(CAR_TYPES)
            self._type_matrix = self._label_matrix(list(CAR_TYPES.values()))
            self._night = self._label_matrix([NIGHT_PHRASES])[0]
            self._scale = float(self.encoder.meta.get("logit_scale", self._scale))

    def _softmax(self, cos: np.ndarray) -> np.ndarray:
        z = self._scale * (cos - cos.max())
        e = np.exp(z)
        return e / e.sum()

    @staticmethod
    def light_measures(rgb: np.ndarray) -> tuple[float, float]:
        """(brake-light glow, glare) as shares of the cut-out's pixels. rgb: HxWx3 uint8."""
        x = rgb.astype(np.float32) / 255
        mx, mn = x.max(axis=2), x.min(axis=2)
        sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
        red_hue = (x[..., 0] >= mx) & (x[..., 0] > x[..., 1] * 1.6) & (x[..., 0] > x[..., 2] * 1.4)
        glow = float(((mx > 0.8) & (sat > 0.5) & red_hue).mean())
        glare = float(((mx > 0.94) & (sat < 0.35)).mean())
        return glow, glare

    def classify(self, embedding: np.ndarray, object_class: str, rgb: np.ndarray | None = None) -> dict:
        """-> {"color", "color_confidence", "vehicle_type", "type_confidence"} (None when unsure).
        rgb: the vehicle cut-out, used to spot poor light (lights flooding the picture)."""
        if object_class not in VEHICLE_CLASSES or not self.available:
            return {}
        self._load()
        emb = embedding.astype(np.float32)
        emb = emb / (np.linalg.norm(emb) + 1e-12)
        out: dict = {}
        poor_light = False
        if rgb is not None:
            glow, glare = self.light_measures(rgb)
            p_night = self._softmax(np.append(self._color_matrix @ emb, self._night @ emb))[-1]
            poor_light = glow > 0.05 or (p_night > 0.75 and glare > 0.05)
        out["poor_light"] = poor_light
        p = self._softmax(self._color_matrix @ emb)
        i = int(p.argmax())
        out["color"], out["color_confidence"] = (
            (COLORS[i], round(float(p[i]), 3)) if p[i] >= MIN_COLOR_CONFIDENCE and not poor_light else (None, None))
        if object_class in FIXED_TYPES:
            out["vehicle_type"], out["type_confidence"] = FIXED_TYPES[object_class], 1.0
        else:
            q = self._softmax(self._type_matrix @ emb)
            j = int(q.argmax())
            out["vehicle_type"], out["type_confidence"] = (
                (self._type_labels[j], round(float(q[j]), 3)) if q[j] >= MIN_TYPE_CONFIDENCE and not poor_light
                else (None, None))
        return out
