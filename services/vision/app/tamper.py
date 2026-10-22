"""Camera tamper detection: covered, blacked out, smeared/out of focus, or moved/turned away.

Checked about once a second per camera on a tiny greyscale copy of the frame (well under a
millisecond). Each camera learns its own "normal" view over the first ~30 s and keeps adapting
slowly while things look normal, so gradual light changes through the day don't alert.

A problem must last `persist` seconds before it's reported (someone walking right past the lens
doesn't count). A camera that was moved and then stays put is accepted as the new normal after
`accept_moved` seconds, so a deliberate re-aim doesn't alert forever.

Known limit: switching the lights off in a room looks the same as covering the lens, so it's
reported as "view blocked or too dark".
"""

from dataclasses import dataclass

import cv2
import numpy as np

SMALL_WIDTH = 160

MESSAGES = {
    "covered": "View blocked or too dark (covered, blacked out or lights off)",
    "blurred": "Picture smeared or out of focus",
    "moved": "Camera moved or turned away",
}


@dataclass
class TamperChange:
    kind: str  # covered | blurred | moved
    started: bool  # True = problem began, False = it cleared
    message: str


class TamperDetector:
    def __init__(self, warmup: float = 30.0, persist: float = 10.0, interval: float = 1.0,
                 accept_moved: float = 300.0, adapt: float = 0.02):
        self.warmup = warmup
        self.persist = persist
        self.interval = interval
        self.accept_moved = accept_moved
        self.adapt = adapt  # how fast the reference follows the scene while normal
        self._ref: np.ndarray | None = None  # learned normal view (float, small grey)
        self._ref_detail = 0.0
        self._started: float | None = None
        self._next = 0.0
        self._suspect: str | None = None
        self._suspect_since = 0.0
        self.active: str | None = None  # the problem currently reported
        self._active_since = 0.0

    @staticmethod
    def _small(image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        small = cv2.resize(image, (SMALL_WIDTH, max(1, round(h * SMALL_WIDTH / w))), interpolation=cv2.INTER_AREA)
        return small if small.ndim == 2 else cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _detail(gray: np.ndarray) -> float:
        return float(cv2.Laplacian(gray, cv2.CV_32F).var())

    def _classify(self, gray: np.ndarray) -> str | None:
        detail = self._detail(gray)
        ref_detail = max(self._ref_detail, 1.0)
        if float(gray.std()) < 8 or (detail < 0.1 * ref_detail and float(gray.mean()) < 35):
            return "covered"
        if detail < 0.25 * ref_detail:
            return "blurred"
        a = gray.astype(np.float32) - gray.mean()
        b = self._ref - self._ref.mean()
        similarity = float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))
        if similarity < 0.4:
            return "moved"
        return None

    def update(self, image: np.ndarray, now: float) -> TamperChange | None:
        """Feed every analysed frame; does real work about once per `interval`."""
        if now < self._next:
            return None
        self._next = now + self.interval
        gray = self._small(image)
        if self._ref is None or self._ref.shape != gray.shape:
            self._ref, self._ref_detail, self._started = gray.astype(np.float32), self._detail(gray), now
            return None
        learning = now - self._started < self.warmup
        kind = None if learning else self._classify(gray)

        if kind is None:
            # Normal: follow slow changes (daylight) and forget any suspicion.
            cv2.accumulateWeighted(gray.astype(np.float32), self._ref, 0.2 if learning else self.adapt)
            self._ref_detail += (0.2 if learning else self.adapt) * (self._detail(gray) - self._ref_detail)
            self._suspect = None
            if self.active and now - self._active_since >= self.persist:
                cleared, self.active = self.active, None
                return TamperChange(cleared, False, f"Back to normal: {MESSAGES[cleared].lower()}")
            return None

        if kind != self._suspect:
            self._suspect, self._suspect_since = kind, now
        lasted = now - self._suspect_since
        if self.active is None and lasted >= self.persist:
            self.active, self._active_since = kind, now
            return TamperChange(kind, True, MESSAGES[kind])
        if self.active == "moved" and kind == "moved" and lasted >= self.accept_moved:
            # Still pointing somewhere new after minutes: accept it as the new normal view.
            self._ref, self._ref_detail = gray.astype(np.float32), self._detail(gray)
            self._suspect, self.active = None, None
            return TamperChange("moved", False, "New camera view accepted as normal")
        if self.active:
            self._active_since = now  # still a problem: restart the "back to normal" clock
        return None
