import numpy as np
import supervision as sv

from app.plates import PlateReader, normalize, vote
from app.sightings import SightingCollector


def test_normalize():
    assert normalize("gr 1234-23") == "GR123423" and normalize(" ab-12.c ") == "AB12C" and normalize(None) == ""


def test_vote_fixes_single_misreads():
    # Real-style reads of one plate across pictures: each has a different wrong character.
    text, conf = vote([("BROJ119", 0.82), ("BRDJ119", 0.75), ("BROJ118", 0.60), ("8ROJ119", 0.55)])
    assert text == "BROJ119" and 0 < conf <= 1
    assert vote([("AB12", 0.9), ("AB12CD", 0.3), ("AB12", 0.8)])[0] == "AB12"  # majority length wins
    assert vote([]) == ("", 0.0) and vote([("--", 0.9)]) == ("", 0.0)


def test_disagreeing_reads_get_low_confidence():
    _, sure = vote([("GR1234", 0.9), ("GR1234", 0.9), ("GR1234", 0.9)])
    _, unsure = vote([("GR1234", 0.9), ("XY9876", 0.9), ("QW5555", 0.9)])
    assert sure > 0.8 and unsure < sure


class FakeReader:
    def __init__(self):
        self.seen = []

    def read_many(self, crops, frame_boxes=None):
        self.seen.append([c.shape for c in crops])
        return "GR123423", 0.9, np.zeros((20, 60, 3), np.uint8), (0.1, 0.5, 0.2, 0.55)


class FakeEmbedder:
    def __call__(self, crop):
        return np.ones(8, np.float32)


def dets(tid, x1, w, cls=3):
    return sv.Detections(xyxy=np.array([[x1, 100, x1 + w, 100 + w * 0.6]], np.float32),
                         confidence=np.array([0.9], np.float32), class_id=np.array([cls]), tracker_id=np.array([tid]))


def test_collector_reads_plates_from_the_largest_views_only_for_vehicles():
    sent = []
    c = SightingCollector(1, sent.append, FakeEmbedder())
    c.plate_reader = FakeReader()
    frame = np.zeros((720, 1280, 3), np.uint8)
    for t, w in enumerate([100, 300, 200, 400, 150]):  # a car getting closer and further away
        c.update(dets(7, 100, w), ["car"], np.array([[300, 400]]), frame, float(t))
    for t in range(5):
        c.update(dets(8, 100, 200, cls=1), ["person"], np.array([[300, 400]]), frame, float(t))
    c.flush()
    c._pool.shutdown(wait=True)
    car = next(s for s in sent if s["object_class"] == "car")
    person = next(s for s in sent if s["object_class"] == "person")
    assert car["plate"] == "GR123423" and car["plate_confidence"] == 0.9 and car["plate_image"]
    assert "plate" not in person
    assert c.plate_reader.seen == [[(240, 400, 3), (180, 300, 3), (120, 200, 3)]]  # 3 largest, biggest first


def test_reader_failure_is_contained(monkeypatch):
    r = PlateReader()
    monkeypatch.setattr(r, "_load", lambda: (_ for _ in ()).throw(RuntimeError("no internet")))
    assert r.read(np.zeros((50, 50, 3), np.uint8)) == ("", 0.0, None) and r.available is False


class Box:
    def __init__(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2


def test_plate_must_look_like_a_plate_on_the_vehicle():
    ok = PlateReader.plausible
    # Real measured plate: centre (0.31, 0.91) of a 497x300 crop, 0.14 x 0.16, aspect ~2.
    assert ok(Box(120, 250, 190, 296), 497, 300, 0.8)
    assert not ok(Box(120, 250, 190, 296), 497, 300, 0.3)    # plate finder unsure
    assert not ok(Box(100, 20, 170, 55), 497, 300, 0.9)      # up on the roof/window: a sticker or sign
    assert not ok(Box(0, 200, 400, 290), 497, 300, 0.9)      # 80% of the car's width: overlay text
    assert not ok(Box(200, 200, 215, 290), 497, 300, 0.9)    # tall and thin: not a plate shape
    assert ok(Box(150, 250, 330, 289), 497, 300, 0.9)        # long (~4.6:1) plate, a third of the car


def test_text_repeating_on_different_vehicles_is_a_sign():
    from app.plates import StaticText
    st = StaticText(window=1800)
    assert not st.is_static("FRESNO", 1, 0)
    assert not st.is_static("FRESN0", 2, 10)   # second vehicle: could still be coincidence
    assert st.is_static("FRESNO", 3, 20)       # third different vehicle: a fixed sign
    assert not st.is_static("GR123423", 4, 30)  # real plates differ
    assert not st.is_static("GR123423", 4, 31)  # the same vehicle again doesn't count
    assert not StaticText(window=10).is_static("FRESNO", 9, 5000)  # old readings expire



def test_overlay_with_random_readings_is_caught_by_place_and_look():
    from app.plates import StaticText
    rng = np.random.default_rng(1)
    overlay = rng.integers(0, 255, (24, 80, 3), np.uint8)  # the same on-screen text every time
    spot = (0.30, 0.80, 0.40, 0.86)
    st = StaticText()
    # The reader makes up different text from the overlay each time ("MXP5170", "EI8S10", ...)
    assert not st.is_static("MXP5170", 1, 0, spot, overlay)
    assert not st.is_static("EI8S10", 2, 5, spot, overlay)
    assert st.is_static("PTPSD0", 3, 9, spot, overlay)


def test_different_cars_at_the_same_barrier_are_kept():
    from app.plates import StaticText
    rng = np.random.default_rng(2)
    spot = (0.45, 0.70, 0.55, 0.75)
    st = StaticText()
    for track, text in enumerate(["GR123423", "AS998821", "GW55520", "GT10019"]):
        plate = rng.integers(0, 255, (24, 80, 3), np.uint8)  # each car's plate looks different
        assert not st.is_static(text, track, track * 30, spot, plate)


def test_rows_of_lights_are_not_plates():
    from app.plates import repetitive
    assert repetitive("L4444444") and repetitive("1111111")
    assert not repetitive("GR123423") and not repetitive("AA11") and not repetitive("GR111123")
