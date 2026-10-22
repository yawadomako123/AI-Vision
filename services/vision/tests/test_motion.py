import numpy as np

from app.motion import MotionGate

H, W = 480, 640


def still():
    return np.full((H, W, 3), 90, np.uint8)


def with_person(x):
    img = still()
    img[150:400, x:x + 60] = 220  # a bright figure
    return img


def test_static_scene_rests_but_keeps_checking():
    g = MotionGate(idle_after=3.0, keepalive=2.0)
    ran = [g.should_analyse(still(), t / 3, tracking=False) for t in range(0, 60)]  # 20 s at 3 fps
    # Analyses for the first ~3 s, then only one frame every ~2 s.
    assert all(ran[:9]) and sum(ran[9:]) <= 9 and sum(ran[9:]) >= 7
    assert g.idle


def test_movement_wakes_it_immediately():
    g = MotionGate()
    for t in range(30):
        g.should_analyse(still(), t / 3, tracking=False)
    assert g.idle
    assert g.should_analyse(with_person(100), 10.1, tracking=False) is True
    assert not g.idle


def test_never_skips_while_someone_is_tracked():
    g = MotionGate()
    img = with_person(100)  # standing perfectly still
    assert all(g.should_analyse(img, t / 3, tracking=True) for t in range(60))


def test_sensor_noise_is_not_motion():
    rng = np.random.default_rng(0)
    g = MotionGate()
    base = still().astype(np.int16)
    noisy = lambda: np.clip(base + rng.integers(-6, 7, base.shape), 0, 255).astype(np.uint8)
    g.moving(noisy())
    assert not any(g.moving(noisy()) for _ in range(20))
