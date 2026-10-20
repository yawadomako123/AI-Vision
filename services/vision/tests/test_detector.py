import numpy as np
import supervision as sv

from app.detector import remove_duplicates


def dets(boxes, confs, classes=None):
    n = len(boxes)
    return sv.Detections(xyxy=np.array(boxes, np.float32), confidence=np.array(confs, np.float32),
                         class_id=np.array(classes or [1] * n))


def test_head_box_inside_body_box_is_dropped():
    # Real case from a phone camera: one man got a face box (0.42) around an upper-body box (0.58).
    d = remove_duplicates(dets([[485, 345, 640, 479], [490, 346, 582, 479]], [0.42, 0.58]))
    assert len(d) == 1 and abs(d.confidence[0] - 0.58) < 1e-6


def test_heavy_overlap_keeps_the_more_confident_box():
    d = remove_duplicates(dets([[100, 100, 200, 300], [105, 110, 205, 310]], [0.7, 0.9]))
    assert len(d) == 1 and abs(d.confidence[0] - 0.9) < 1e-6


def test_separate_people_and_a_small_person_behind_are_kept():
    d = remove_duplicates(dets([[0, 0, 100, 300], [300, 0, 400, 300],   # two people side by side
                                [20, 20, 45, 80]],                       # small, far away, inside box 0
                               [0.9, 0.8, 0.6]))
    assert len(d) == 3


def test_different_classes_never_merge():
    d = remove_duplicates(dets([[0, 0, 100, 100], [2, 2, 100, 100]], [0.9, 0.8], classes=[1, 3]))
    assert len(d) == 2
