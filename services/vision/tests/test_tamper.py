import cv2
import numpy as np

from app.tamper import TamperDetector


def scene(seed=0, brightness=0):
    """A textured 'room': random shapes, lightly blurred, like a real view."""
    rng = np.random.default_rng(seed)
    img = np.full((360, 640, 3), 120, np.uint8)
    for _ in range(60):
        x, y = rng.integers(0, 600), rng.integers(0, 330)
        cv2.rectangle(img, (x, y), (x + rng.integers(10, 80), y + rng.integers(10, 60)), rng.integers(30, 230, 3).tolist(), -1)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    return np.clip(img.astype(int) + brightness, 0, 255).astype(np.uint8)


def run(det, frames_by_second):
    """Feed one frame per second; returns the changes reported."""
    out = []
    for t, img in frames_by_second:
        c = det.update(img, float(t))
        if c:
            out.append((t, c.kind, c.started))
    return out


NORMAL = scene()


def learned():
    d = TamperDetector()
    assert run(d, [(t, NORMAL) for t in range(40)]) == []  # warm-up + normal: nothing
    return d


def test_covered_lens_alerts_after_10s_then_clears():
    d = learned()
    black = np.full_like(NORMAL, 6)
    changes = run(d, [(t, black) for t in range(40, 60)] + [(t, NORMAL) for t in range(60, 80)])
    assert changes[0][1:] == ("covered", True) and 49 <= changes[0][0] <= 51
    assert changes[1][1:] == ("covered", False)


def test_smeared_lens():
    d = learned()
    smeared = cv2.GaussianBlur(NORMAL, (31, 31), 0)
    assert run(d, [(t, smeared) for t in range(40, 60)])[0][1:] == ("blurred", True)


def test_moved_camera_alerts_then_accepts_new_view():
    d = learned()
    elsewhere = scene(seed=7)
    changes = run(d, [(t, elsewhere) for t in range(40, 400)])
    assert changes[0][1:] == ("moved", True)
    assert changes[-1][1:] == ("moved", False)  # accepted as the new normal after ~5 min
    assert run(d, [(t, elsewhere) for t in range(400, 420)]) == []


def test_brief_block_and_slow_daylight_change_dont_alert():
    d = learned()
    black = np.full_like(NORMAL, 6)
    frames = [(t, black) for t in range(40, 46)]  # someone right in front of the lens for 6 s
    frames += [(t, scene(brightness=(t - 46) // 20)) for t in range(46, 1000)]  # +47 grey levels over ~16 min
    assert run(d, frames) == []
