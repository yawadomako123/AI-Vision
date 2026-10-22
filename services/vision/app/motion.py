"""Motion skip: rest the AI when a camera sees nothing moving and nobody is being tracked.

Before each detector run, a tiny blurred greyscale copy of the frame (160 px wide) is compared
with the previous one. If almost no pixels changed, nothing is being tracked, and that has been
true for a few seconds, the detector is skipped. A safety check still runs every couple of
seconds, so slow movement, someone standing very still, or a lighting change can't slip through.

Costs well under a millisecond per frame, against ~100 ms for the detector, so a quiet camera
uses a small fraction of the GPU. While anyone is tracked it never skips, so loitering,
occupancy and counting keep working.
"""

import cv2
import numpy as np

SMALL_WIDTH = 160


class MotionGate:
    def __init__(self, pixel_threshold: int = 25, min_changed: float = 0.003,
                 idle_after: float = 3.0, keepalive: float = 2.0):
        self.pixel_threshold = pixel_threshold  # grey-level change that counts as "changed"
        self.min_changed = min_changed  # share of pixels that must change to count as motion
        self.idle_after = idle_after  # seconds of stillness before skipping starts
        self.keepalive = keepalive  # while idle, still analyse one frame this often
        self._prev: np.ndarray | None = None
        self._last_active = -1e18
        self._last_run = -1e18
        self.idle = False

    def _small(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        small = cv2.resize(image, (SMALL_WIDTH, max(1, round(h * SMALL_WIDTH / w))), interpolation=cv2.INTER_AREA)
        gray = small if small.ndim == 2 else cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (5, 5), 0)

    def moving(self, image: np.ndarray) -> bool:
        small = self._small(image)
        prev, self._prev = self._prev, small
        if prev is None or prev.shape != small.shape:
            return True
        changed = cv2.absdiff(small, prev) > self.pixel_threshold
        return float(changed.mean()) >= self.min_changed

    def should_analyse(self, image: np.ndarray, now: float, tracking: bool) -> bool:
        """tracking: the last analysed frame had objects in it."""
        if self.moving(image) or tracking:
            self._last_active = now
        if now - self._last_active < self.idle_after or now - self._last_run >= self.keepalive:
            self._last_run = now
            self.idle = now - self._last_active >= self.idle_after
            return True
        self.idle = True
        return False
