"""Privacy masks: areas blacked out the moment a frame arrives (a neighbour's window, a street).

Applied in the frame reader, so everything downstream (detection, motion skip, tamper checks,
snapshots, alert clips, search images, live-wall previews) only ever sees the masked picture.
The original stream from the camera (and MediaMTX's live video) is not changed; the dashboard
draws the masks over that video instead.
"""

import threading

import cv2
import numpy as np


class PrivacyMask:
    def __init__(self) -> None:
        self._polygons: list[list[list[float]]] = []  # normalized points
        self._cache: tuple[tuple[int, int], np.ndarray] | None = None  # (h, w) -> bool mask
        self._lock = threading.Lock()

    def set(self, polygons: list[list[list[float]]]) -> bool:
        """Returns True if the masks changed."""
        with self._lock:
            if polygons == self._polygons:
                return False
            self._polygons, self._cache = [list(map(list, p)) for p in polygons], None
            return True

    def __bool__(self) -> bool:
        return bool(self._polygons)

    def covered_fraction(self) -> float:
        """Share of the last frame size that's masked (0 before the first frame)."""
        with self._lock:
            return float(self._cache[1].mean()) if self._cache is not None else 0.0

    def apply(self, image: np.ndarray) -> np.ndarray:
        """Black out the masked areas, in place (the reader owns the frame)."""
        with self._lock:
            if not self._polygons:
                return image
            h, w = image.shape[:2]
            if self._cache is None or self._cache[0] != (h, w):
                m = np.zeros((h, w), np.uint8)
                for poly in self._polygons:
                    pts = np.array([[x * w, y * h] for x, y in poly]).round().astype(np.int32)
                    cv2.fillPoly(m, [pts], 255)
                self._cache = ((h, w), m.astype(bool))
            image[self._cache[1]] = 0
            return image
