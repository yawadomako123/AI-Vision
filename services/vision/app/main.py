"""Headless vision worker: ingest -> detect -> track, for every configured camera.

    uv run python -m app.main                      # cameras from .env CAMERA_SOURCES
    uv run python -m app.main --show               # also open preview windows
    uv run python -m app.main --source ../../samples/door.mp4 --show

Prints one JSON line per camera per second. Zone/line rules and event publishing plug in
after tracking (next milestone).
"""

import argparse
import json
import logging
import time

import cv2
import supervision as sv

from app.config import settings
from app.detector import COCO_CLASSES, Detector
from app.frame_reader import FrameReader

log = logging.getLogger("vision")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", help="RTSP URL or video file (repeatable)")
    parser.add_argument("--show", action="store_true", help="show annotated preview windows")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    sources = args.source or settings.sources
    detector = Detector(
        settings.model_path, settings.inference_device, settings.detection_confidence, settings.classes
    )
    log.info("Model %s, input %spx, provider %s", settings.model_path.name, detector.resolution, detector.provider)

    cameras = []
    for i, src in enumerate(sources, start=1):
        cam_id = f"cam{i}"
        cameras.append({
            "id": cam_id,
            "reader": FrameReader(cam_id, src).start(),
            # frame_rate = our sampling rate, so lost_track_buffer is measured in sampled frames
            "tracker": sv.ByteTrack(frame_rate=max(1, round(settings.inference_fps))),
            "next_due": 0.0,
            "stats": {"inferences": 0, "infer_ms": 0.0, "last_report": time.monotonic()},
        })
        log.info("%s <- %s", cam_id, src)

    box_annotator = sv.BoxAnnotator()
    label_annotator = sv.LabelAnnotator(text_scale=0.5)
    interval = 1.0 / settings.inference_fps

    try:
        while True:
            now = time.monotonic()
            for cam in cameras:
                if now < cam["next_due"]:
                    continue
                frame = cam["reader"].latest(timeout=0)
                if frame is None:
                    continue
                cam["next_due"] = now + interval

                t0 = time.perf_counter()
                detections = cam["tracker"].update_with_detections(detector(frame.image))
                infer_ms = (time.perf_counter() - t0) * 1000

                # Derived from class_id: the tracker drops `data` when nothing is detected.
                names = [COCO_CLASSES.get(int(c), str(c)) for c in detections.class_id]
                stats = cam["stats"]
                stats["inferences"] += 1
                stats["infer_ms"] += infer_ms

                if args.show:
                    labels = [
                        f"#{tid} {name} {conf:.2f}"
                        for tid, name, conf in zip(detections.tracker_id, names, detections.confidence)
                    ]
                    img = box_annotator.annotate(frame.image.copy(), detections)
                    img = label_annotator.annotate(img, detections, labels)
                    cv2.imshow(cam["id"], img)

                if now - stats["last_report"] >= 1.0:
                    counts: dict[str, int] = {}
                    for name in names:
                        counts[name] = counts.get(name, 0) + 1
                    elapsed = now - stats["last_report"]
                    print(json.dumps({
                        "camera": cam["id"],
                        "status": cam["reader"].status,
                        "infer_fps": round(stats["inferences"] / elapsed, 2),
                        "avg_infer_ms": round(stats["infer_ms"] / stats["inferences"], 1),
                        "frame_age_ms": round((time.time() - frame.captured_at) * 1000),
                        "objects": counts,
                        "track_ids": [int(t) for t in detections.tracker_id],
                    }), flush=True)
                    stats.update(inferences=0, infer_ms=0.0, last_report=now)

            if args.show and cv2.waitKey(1) & 0xFF == ord("q"):
                break
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        for cam in cameras:
            cam["reader"].stop()
        if args.show:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
