"""Stable numbers for tracked objects ("#1 person", "#2 car"…), and recognising people who come back.

The tracker (ByteTrack) follows boxes by position and motion only. When someone leaves the picture
(or is hidden for longer than the tracker remembers) and comes back, the tracker starts a new
track. Raw tracker ids also climb quickly (#21, #25…), which reads like "25 people".

`Identities` sits between the tracker and everything else (rules, events, search, live view):
- each new track gets the next small number for its camera;
- with a re-identification model (an "appearance fingerprint" per person crop), a new person track
  is first compared with people who left recently. A confident match gets their old number back.
  Matches need a clear winner: two people in near-identical clothes stay apart rather than merge.

The raw tracker id is kept in `detections.data["raw_track"]`: line crossing compares positions
of the same track, and a returning person must not "cross" a line between leaving and coming back.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

log = logging.getLogger(__name__)

REID_CLASSES = {"person"}
REMEMBER_SECONDS = 120.0   # people who left are recognised for this long
MATCH_THRESHOLD = 0.62     # cosine similarity of appearance fingerprints (tuned on test clips)
MATCH_MARGIN = 0.05        # best match must beat the runner-up by this much
UPDATE_EVERY = 1.0         # seconds between fingerprint refreshes of a visible person
MIN_CROP_HEIGHT = 48       # pixels; smaller crops give unreliable fingerprints
GALLERY_SIZE = 12          # fingerprints kept per person (different poses and lighting)
TOP_VIEWS = 3              # a match is judged on this many best-matching stored views
CONSISTENT = 0.45          # a new view must look this much like the person to be learned


class ReidModel:
    """Intel Open Model Zoo person-reidentification-retail-0288 (Apache-2.0) on OpenVINO:
    a 256x128 BGR person crop -> a 256-number appearance fingerprint (unit length)."""

    def __init__(self, model_dir: Path, device: str = "GPU"):
        import openvino as ov

        core = ov.Core()
        core.set_property({"CACHE_DIR": str(model_dir / ".ov_cache")})
        model = core.read_model(model_dir / "person-reidentification-retail-0288.xml")
        try:
            compiled = core.compile_model(model, device)
            self.provider = f"OpenVINO {device}"
        except Exception as exc:  # noqa: BLE001
            log.warning("re-identification on %s unavailable (%s); using CPU", device, exc)
            compiled = core.compile_model(model, "CPU")
            self.provider = "OpenVINO CPU"
        self._request = compiled.create_infer_request()
        self._out = compiled.output(0)

    def __call__(self, crop_bgr: np.ndarray) -> np.ndarray:
        x = cv2.resize(crop_bgr, (128, 256), interpolation=cv2.INTER_LINEAR).transpose(2, 0, 1)[None]
        emb = np.asarray(self._request.infer({0: x.astype(np.float32)})[self._out], np.float32)[0]
        return emb / (np.linalg.norm(emb) + 1e-12)


@dataclass
class _Person:
    number: int
    cls: str
    fingerprints: list[np.ndarray] = field(default_factory=list)
    last_seen: float = 0.0
    last_fingerprint: float = -1e9

    def similarity(self, emb: np.ndarray) -> float:
        # The average of the best few stored views: front and back views still match, but one
        # stray fingerprint (e.g. from a moment the tracker mixed two people up) can't decide alone.
        if not self.fingerprints:
            return -1.0
        sims = sorted((float(f @ emb) for f in self.fingerprints), reverse=True)
        return float(np.mean(sims[:TOP_VIEWS]))

    def remember(self, emb: np.ndarray, now: float) -> None:
        # Learn only views that look like this person: when two people cross, the tracker can swap
        # them for a moment, and learning that view would make the other person match later.
        if len(self.fingerprints) >= TOP_VIEWS and self.similarity(emb) < CONSISTENT:
            return
        self.fingerprints.append(emb)
        del self.fingerprints[:-GALLERY_SIZE]
        self.last_fingerprint = now


class Identities:
    def __init__(self, reid=None, remember_seconds: float = REMEMBER_SECONDS,
                 threshold: float = MATCH_THRESHOLD, margin: float = MATCH_MARGIN):
        self.reid, self.remember_seconds, self.threshold, self.margin = reid, remember_seconds, threshold, margin
        self.by_track: dict[int, int] = {}   # raw tracker id -> stable number
        self.people: dict[int, _Person] = {}  # stable number -> what we know
        self.next_number = 1
        self.recognised = 0                   # returning people matched (for the logs)

    def assign(self, detections: sv.Detections, names: list[str], image: np.ndarray | None, now: float) -> sv.Detections:
        """Replace raw tracker ids with stable numbers (raw ids kept in data["raw_track"])."""
        if detections.tracker_id is None or len(detections) == 0:
            self._forget(now)
            return detections
        raw = detections.tracker_id.astype(int)
        visible = {self.by_track[t] for t in raw if t in self.by_track}
        new = [i for i, t in enumerate(raw) if t not in self.by_track]

        # New tracks: a returning person, or someone new. Most confident matches are settled first.
        fingerprints = {i: self._fingerprint(detections.xyxy[i], image, detections.xyxy)
                        for i in new if self.reid is not None and names[i] in REID_CLASSES}
        options = []
        for i, emb in fingerprints.items():
            if emb is None:
                continue
            scored = sorted(((p.similarity(emb), n) for n, p in self.people.items()
                             if p.cls == names[i] and n not in visible and now - p.last_seen <= self.remember_seconds),
                            reverse=True)
            if scored and scored[0][0] >= self.threshold and \
                    (len(scored) == 1 or scored[0][0] - scored[1][0] >= self.margin):
                options.append((scored[0][0], i, scored[0][1]))
        taken: set[int] = set()
        for _, i, number in sorted(options, reverse=True):
            if i in taken or number in visible:
                continue
            # Retire the person's old track, so a late revival of it can't show a second "#n".
            self.by_track = {t: n for t, n in self.by_track.items() if n != number}
            self.by_track[int(raw[i])] = number
            visible.add(number)
            taken.add(i)
            self.recognised += 1
            log.info("re-identified a returning %s as #%d", names[i], number)
        for i in new:
            if int(raw[i]) not in self.by_track:
                number = self.next_number
                self.next_number += 1
                self.by_track[int(raw[i])] = number
                self.people[number] = _Person(number, names[i])

        stable = np.array([self.by_track[int(t)] for t in raw], dtype=int)
        for i, number in enumerate(stable):
            person = self.people.setdefault(int(number), _Person(int(number), names[i]))
            person.last_seen = now
            emb = fingerprints.get(i)
            if emb is None and self.reid is not None and names[i] in REID_CLASSES \
                    and now - person.last_fingerprint >= UPDATE_EVERY:
                emb = self._fingerprint(detections.xyxy[i], image, detections.xyxy)
            if emb is not None:
                person.remember(emb, now)

        detections.data["raw_track"] = raw
        detections.tracker_id = stable
        self._forget(now)
        return detections

    def tracker_reset(self) -> None:
        """A new tracker numbers its tracks from 1 again: forget the old track ids (but keep the
        people, so whoever is still there is recognised again)."""
        self.by_track.clear()

    def _fingerprint(self, box: np.ndarray, image: np.ndarray | None, boxes: np.ndarray) -> np.ndarray | None:
        """Fingerprint of a clean crop only: big enough, and not overlapping someone else
        (a crop showing two people would teach the wrong look)."""
        if image is None:
            return None
        x1, y1, x2, y2 = (int(round(v)) for v in box)
        h, w = image.shape[:2]
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        if y2 - y1 < MIN_CROP_HEIGHT or x2 - x1 < MIN_CROP_HEIGHT // 4:
            return None
        if len(boxes) > 1 and (sv.box_iou_batch(np.asarray([box]), boxes)[0] > 0.1).sum() > 1:
            return None
        return self.reid(image[y1:y2, x1:x2])

    def _forget(self, now: float) -> None:
        gone = [n for n, p in self.people.items() if now - p.last_seen > self.remember_seconds]
        for n in gone:
            del self.people[n]
        if gone:
            dead = set(gone)
            self.by_track = {t: n for t, n in self.by_track.items() if n not in dead}
