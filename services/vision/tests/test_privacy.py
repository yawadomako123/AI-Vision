import numpy as np

from app.privacy import PrivacyMask

RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]


def test_masked_area_is_blacked_out_rest_untouched():
    m = PrivacyMask()
    assert not m and m.set([RIGHT_HALF]) is True and m
    img = np.full((100, 200, 3), 200, np.uint8)
    m.apply(img)
    assert img[:, :99].min() == 200  # left half untouched
    assert img[:, 101:].max() == 0  # right half black


def test_set_reports_changes_and_follows_frame_size():
    m = PrivacyMask()
    m.set([RIGHT_HALF])
    assert m.set([RIGHT_HALF]) is False  # same masks: nothing to rebuild
    small = m.apply(np.full((50, 80, 3), 255, np.uint8))
    assert small[:, 41:].max() == 0 and small[:, :39].min() == 255
    assert m.set([]) is True
    assert m.apply(np.full((10, 10, 3), 9, np.uint8)).min() == 9  # no masks: unchanged


def test_detector_never_sees_a_person_inside_a_mask():
    from pathlib import Path

    import av
    import pytest

    from app.config import settings
    from app.detector import Detector

    clip = Path(__file__).resolve().parents[3] / "samples" / "e2e-person.mp4"
    if not settings.model_path.exists() or not clip.exists():
        pytest.skip("needs the model and samples/e2e-person.mp4")
    det = Detector(settings.model_path, "CPU", confidence=0.5, classes={"person"})
    with av.open(str(clip)) as c:
        frames = [f.to_ndarray(format="bgr24") for i, f in enumerate(c.decode(video=0)) if i % 10 == 0][:12]
    seen = [len(det(f)) for f in frames]
    assert sum(seen) > 0  # the person is found normally
    whole = PrivacyMask()
    whole.set([[[0, 0], [1, 0], [1, 1], [0, 1]]])
    assert sum(len(det(whole.apply(f.copy()))) for f in frames) == 0
