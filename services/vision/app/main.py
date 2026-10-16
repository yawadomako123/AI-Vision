"""Vision worker: ingest -> detect -> track -> rules -> events, for every configured camera.

    uv run python -m app.main                      # cameras from .env CAMERA_SOURCES
    uv run python -m app.main --show               # also open preview windows
    uv run python -m app.main --source ../../samples/door.mp4 --show

Prints one status JSON line per camera per second. Rules come from config/rules.json
(draw them with `python -m app.draw_rules`) and are reloaded when the file changes.
Events go to storage/events/events.jsonl with annotated snapshots.
"""

import argparse
import json
import logging
import time

import cv2
import supervision as sv

from app.config import settings
from app.detector import COCO_CLASSES, Detector
from app.event_publisher import EventPublisher
from app.frame_reader import FrameReader
from app.overlay import draw_rules
from app.rule_engine import RuleEngine, RuleHit, RulesFile, object_positions

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

    rules_file = RulesFile(settings.rules_path)
    rules_file.changed()
    publisher = EventPublisher(settings.event_storage_path)

    cameras = []
    for i, src in enumerate(sources, start=1):
        cam_id = f"cam{i}"
        cameras.append({
            "id": cam_id,
            "reader": FrameReader(cam_id, src).start(),
            # frame_rate = our sampling rate, so lost_track_buffer is measured in sampled frames
            "tracker": sv.ByteTrack(frame_rate=max(1, round(settings.inference_fps))),
            "engine": RuleEngine(rules_file.for_camera(cam_id)),
            "started_at": time.time(),
            "offline_alerted": False,
            "last_image": None,
            "next_due": 0.0,
            "stats": {"inferences": 0, "infer_ms": 0.0, "last_report": time.monotonic()},
        })
        log.info("%s <- %s (%d rules)", cam_id, src, len(rules_file.for_camera(cam_id)))

    box_annotator = sv.BoxAnnotator()
    label_annotator = sv.LabelAnnotator(text_scale=0.5)
    interval = 1.0 / settings.inference_fps
    next_rules_check = 0.0

    try:
        while True:
            now = time.monotonic()
            if now >= next_rules_check:
                next_rules_check = now + 2.0
                if rules_file.changed():
                    for cam in cameras:
                        cam["engine"].set_rules(rules_file.for_camera(cam["id"]))

            for cam in cameras:
                check_health(cam, publisher)
                if now < cam["next_due"]:
                    continue
                frame = cam["reader"].latest(timeout=0)
                if frame is None:
                    continue
                cam["next_due"] = now + interval
                cam["last_image"] = frame.image

                t0 = time.perf_counter()
                detections = cam["tracker"].update_with_detections(detector(frame.image))
                infer_ms = (time.perf_counter() - t0) * 1000

                h, w = frame.image.shape[:2]
                engine = cam["engine"]
                for hit in engine.update(detections, w, h, frame.captured_at):
                    publisher.publish(cam["id"], hit, frame.image, engine.rules)

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
                    img = draw_rules(frame.image.copy(), engine.rules)
                    img = box_annotator.annotate(img, detections)
                    img = label_annotator.annotate(img, detections, labels)
                    # The point the rules test against zones/lines.
                    for x, y, _, _ in object_positions(detections, h).xyxy.astype(int):
                        cv2.circle(img, (x, y), 7, (0, 255, 255), -1)
                        cv2.circle(img, (x, y), 7, (0, 0, 0), 2)
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


def check_health(cam: dict, publisher: EventPublisher) -> None:
    """Raise camera_offline after a sustained outage, and camera_online when it recovers."""
    reader = cam["reader"]
    wall = time.time()
    if reader.status == "online":
        if cam["offline_alerted"]:
            cam["offline_alerted"] = False
            publisher.publish(cam["id"], RuleHit(rule=None, event_type="camera_online", occurred_at=wall), None)
        return
    last_ok = reader.last_frame_at or cam["started_at"]
    down_for = wall - last_ok
    if not cam["offline_alerted"] and down_for >= settings.offline_alert_seconds:
        cam["offline_alerted"] = True
        hit = RuleHit(rule=None, event_type="camera_offline", occurred_at=wall,
                      details={"offline_seconds": round(down_for, 1), "reconnects": reader.reconnects})
        # Last good frame (if any) shows what the camera saw before it went down.
        publisher.publish(cam["id"], hit, cam["last_image"])


if __name__ == "__main__":
    main()
