"""Measure how well people keep their number when they leave the picture and come back.

The clip plays a stretch of video, then the empty scene for a few seconds (everyone "left"), then
replays part of the same stretch: the same people come back, so the right answer is known.
For every person in the replay we check the number they got:
  kept  = the number they had the first time (recognised)
  new   = a number nobody had the first time (not recognised: harmless, just a new label)
  wrong = the number of a DIFFERENT person from the first time (a false merge: the bad error)

Run from services/vision:
  uv run python ../../scripts/reid_eval.py ../../samples/vtest.avi
(vtest.avi: https://github.com/opencv/opencv/raw/4.x/samples/data/vtest.avi, BSD licence)
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "vision"))

from app.config import settings  # noqa: E402
from app.detector import COCO_CLASSES, Detector  # noqa: E402
from app.identity import Identities, ReidModel  # noqa: E402


def read_frames(path: str) -> tuple[list[np.ndarray], float]:
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 10.0
    frames = []
    while True:
        ok, img = cap.read()
        if not ok:
            break
        frames.append(img)
    return frames, fps


def run(frames, fps, schedule, detector, tracker_seconds, reid, threshold, analysis_fps=3.0):
    """schedule: list of (source frame index or None for the empty scene). Returns per-step results."""
    tracker = sv.ByteTrack(frame_rate=max(1, round(analysis_fps)), lost_track_buffer=round(30 * tracker_seconds),
                           minimum_consecutive_frames=2)
    ids = Identities(reid, threshold=threshold)
    out = []
    for step, (src, image) in enumerate(schedule):
        now = step / analysis_fps
        dets = tracker.update_with_detections(detector(image))
        names = [COCO_CLASSES.get(int(c), str(c)) for c in dets.class_id]
        dets = ids.assign(dets, names, image, now)
        keep = np.array([n == "person" for n in names], bool) if len(names) else np.zeros(0, bool)
        out.append((src, dets[keep]))
    return out, ids


def score(results, replay_from: int, frames=None, dump: Path | None = None):
    """Compare each replayed person with the same frame the first time round."""
    first: dict[int, sv.Detections] = {}
    crops: dict[int, np.ndarray] = {}  # number -> a first-time crop of whoever had it
    sheet = []
    seen_first: set[int] = set()
    kept = new = wrong = 0
    phase = "first"
    for src, dets in results:
        if src is None:
            phase = "replay"
            continue
        if phase == "first":
            first[src] = dets
            seen_first.update(int(t) for t in dets.tracker_id)
            if frames is not None:
                for k, t in enumerate(dets.tracker_id):
                    crops.setdefault(int(t), _crop(frames[src], dets.xyxy[k]))
            continue
        if src not in first or len(dets) == 0 or len(first[src]) == 0:
            continue
        iou = sv.box_iou_batch(dets.xyxy, first[src].xyxy)
        for i in range(len(dets)):
            j = int(iou[i].argmax())
            if iou[i, j] < 0.7:
                continue
            got, was = int(dets.tracker_id[i]), int(first[src].tracker_id[j])
            if got == was:
                kept += 1
            elif got in seen_first:
                wrong += 1
                if dump is not None and len(sheet) < 24:
                    # replay crop | who had this number first | who this really is (first time)
                    sheet.append(np.hstack([_crop(frames[src], dets.xyxy[i]), crops[got], _crop(frames[src], first[src].xyxy[j])]))
            else:
                new += 1
    if dump is not None and sheet:
        rows = [np.hstack(sheet[r:r + 4] + [np.zeros_like(sheet[0])] * (4 - len(sheet[r:r + 4])))
                for r in range(0, len(sheet), 4)]
        cv2.imwrite(str(dump), np.vstack(rows))
    return kept, new, wrong


def _crop(image: np.ndarray, box: np.ndarray) -> np.ndarray:
    x1, y1, x2, y2 = (int(v) for v in box)
    c = image[max(0, y1):max(y1 + 1, y2), max(0, x1):max(x1 + 1, x2)]
    return cv2.copyMakeBorder(cv2.resize(c, (60, 140)), 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(255, 255, 255))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--first", type=float, default=45.0, help="seconds played first")
    ap.add_argument("--gap", type=float, default=4.0, help="seconds of empty scene")
    ap.add_argument("--replay-from", type=float, default=15.0, help="replay starts here (seconds)")
    ap.add_argument("--dump", type=Path, help="save picture triples of the 'wrong' cases (re-id, 0.62) here")
    args = ap.parse_args()

    frames, fps = read_frames(args.clip)
    background = np.median(np.stack(frames[:: max(1, len(frames) // 60)]), axis=0).astype(np.uint8)
    step = max(1, round(fps / 3.0))
    first_end = min(len(frames), int(args.first * fps))
    schedule = [(i, frames[i]) for i in range(0, first_end, step)]
    schedule += [(None, background)] * round(args.gap * 3)
    schedule += [(i, frames[i]) for i in range(int(args.replay_from * fps), first_end, step)]

    detector = Detector(settings.model_path, settings.inference_device, 0.5, settings.classes)
    reid = ReidModel(settings.reid_model_dir, settings.reid_device)
    print(f"{len(schedule)} analysed frames; replay of {first_end / fps - args.replay_from:.0f} s after a "
          f"{args.gap:.0f} s gap\n")
    print(f"{'setup':38} {'numbers':>7} {'kept':>5} {'new':>5} {'wrong':>5}")
    setups = [("before: 1 s memory, no re-id", 1.0, None, 0),
              ("step 1: 3 s memory, small numbers", 3.0, None, 0)]
    setups += [(f"step 2: + re-id (threshold {t:.2f})", 3.0, reid, t) for t in (0.55, 0.62, 0.70)]
    for label, memory, model, threshold in setups:
        results, ids = run(frames, fps, schedule, detector, memory, model, threshold or 0.62)
        dump = args.dump if (args.dump and model is not None and threshold == 0.62) else None
        kept, new, wrong = score(results, int(args.replay_from * fps), frames, dump)
        numbers = len({int(t) for _, d in results for t in d.tracker_id})
        print(f"{label:38} {numbers:>7} {kept:>5} {new:>5} {wrong:>5}")


if __name__ == "__main__":
    main()
