import time

import av
import numpy as np

from app.clips import ClipConfig, ClipRecorder, _encoder, encode_clip


def frame(i: int, w: int = 1280, h: int = 720) -> np.ndarray:
    img = np.zeros((h, w, 3), np.uint8)
    img[:, (i * 20) % w:(i * 20) % w + 40] = 255  # a moving bar
    return img


def wait_for_file(path, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    return path.exists()


def test_encode_clip_is_small_and_playable(tmp_path):
    out = tmp_path / "c.mp4"
    encode_clip([(i / 6, frame(i, 640, 480)) for i in range(48)], out, fps=6, crf=32)
    assert out.exists() and not (tmp_path / "c.part.mp4").exists()
    with av.open(str(out)) as c:
        v = c.streams.video[0]
        assert (v.codec_context.name, v.width, v.height) == ("h264", 640, 480)
        assert sum(1 for _ in c.decode(v)) == 48
    assert out.stat().st_size < 300_000


def clip_seconds(path) -> float:
    with av.open(str(path)) as c:
        return c.duration / 1_000_000


def test_playback_uses_real_time_even_with_few_frames(tmp_path):
    """A busy machine may deliver 3 fps instead of 6: the clip must still last the real 8 s."""
    out = tmp_path / "sparse.mp4"
    encode_clip([(i / 3, frame(i, 640, 480)) for i in range(25)], out, fps=6, crf=32)  # 8 s at 3 fps
    assert 7.5 <= clip_seconds(out) <= 8.6


def test_recorder_keeps_preroll_and_post(tmp_path):
    rec = ClipRecorder(ClipConfig(fps=6, pre_seconds=3, post_seconds=5))
    t0 = 1000.0
    for i in range(60):  # 10 s of 6 fps frames before the alert -> only the last 3 s are kept
        rec.add_frame(frame(i), t0 + i / 6)
    alert = t0 + 10
    path = rec.start(tmp_path / "e.mp4", alert)
    assert path == tmp_path / "e.mp4"
    assert rec.start(tmp_path / "other.mp4", alert + 1) == path  # alerts during a clip share it
    for i in range(60, 60 + 6 * 7):
        rec.add_frame(frame(i), t0 + i / 6)
    assert wait_for_file(path)
    _encoder.submit(lambda: None).result()  # wait for the encoder thread to finish
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        n = sum(1 for _ in c.decode(v))
        assert v.width == 640  # downscaled from 1280
    assert 3 * 6 <= n <= (3 + 7) * 6  # pre-roll + post (extended by the second alert)


def test_disabled_records_nothing(tmp_path):
    rec = ClipRecorder(ClipConfig(enabled=False))
    rec.add_frame(frame(0), 1.0)
    assert rec.start(tmp_path / "e.mp4", 1.0) is None


def test_stalled_camera_still_produces_clip(tmp_path):
    rec = ClipRecorder(ClipConfig(fps=6, post_seconds=5))
    for i in range(12):
        rec.add_frame(frame(i), 100 + i / 6)
    path = rec.start(tmp_path / "s.mp4", 102.0)
    rec.tick(102.0 + 5 + 3)  # no frames arrived after the alert
    assert wait_for_file(path)
