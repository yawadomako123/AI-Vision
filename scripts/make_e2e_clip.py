"""Build a test clip of a person moving left <-> right, for the end-to-end test.

Takes any image that contains a person (e.g. an event snapshot), finds the person with the
detector, and slides that cut-out across a plain background. Output stays in samples/
(git-ignored).

Run from services/vision:
    uv run python ../../scripts/make_e2e_clip.py --image ../../storage/events/2026/10/16/<snapshot>.jpg
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "vision"))
from app.config import settings  # noqa: E402
from app.detector import Detector  # noqa: E402

W, H, FPS = 640, 480, 15


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True, help="image containing a person")
    p.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "samples" / "e2e-person.mp4"))
    args = p.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"cannot read {args.image}")
    det = Detector(settings.model_path, settings.inference_device, 0.4, {"person"})
    found = det(img)
    if not len(found):
        raise SystemExit("no person found in that image")
    x1, y1, x2, y2 = found.xyxy[np.argmax(found.confidence)].astype(int)
    person = img[max(0, y1):y2, max(0, x1):x2]

    # Scale the cut-out to ~55% of frame height and stand it a bit above the bottom edge,
    # so its "feet" (box bottom) are visible like a camera mounted high.
    scale = (H * 0.55) / person.shape[0]
    person = cv2.resize(person, None, fx=scale, fy=scale)
    ph, pw = person.shape[:2]
    top = H - ph - 30

    bg = np.full((H, W, 3), 70, np.uint8)
    cv2.rectangle(bg, (0, int(H * 0.65)), (W, H), (95, 95, 95), -1)  # "floor"

    # left -> right (6 s), pause (2 s), right -> left (6 s), pause (2 s)
    xs = list(np.linspace(10, W - pw - 10, 6 * FPS)) + [W - pw - 10] * (2 * FPS)
    xs += xs[: 6 * FPS][::-1] + [10] * (2 * FPS)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for x in xs:
        frame = bg.copy()
        x = int(x)
        frame[top:top + ph, x:x + pw] = person
        writer.write(frame)
    writer.release()

    # Sanity check: the detector must see the person in the generated frames.
    check = bg.copy()
    check[top:top + ph, W // 2 - pw // 2:W // 2 - pw // 2 + pw] = person
    seen = det(check)
    print(f"wrote {out} ({len(xs) / FPS:.0f} s), person detected in test frame: {len(seen) > 0} "
          f"(conf {float(seen.confidence.max()) if len(seen) else 0:.2f})")


if __name__ == "__main__":
    main()
