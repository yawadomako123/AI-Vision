"""Vision worker: ingest -> detect -> track -> rules -> events, for every camera.

    uv run python -m app.main                      # cameras from the API (API_BASE_URL) or CAMERA_SOURCES
    uv run python -m app.main --show               # also open preview windows
    uv run python -m app.main --source ../../samples/door.mp4 --show   # one-off file, no API

API mode (API_BASE_URL set): cameras are fetched from the API every CAMERA_REFRESH_SECONDS
(added/removed/changed cameras are picked up live), camera status is reported every 5 s and
events are sent to the API. Events and snapshots are always written to storage/events too.

Rules come from config/rules.json keyed by "cam<id>" (draw them with `python -m app.draw_rules`)
and are reloaded when the file changes.
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone

import cv2
import httpx
import supervision as sv

from app.api_client import ApiClient
from app.config import settings
from app.detector import COCO_CLASSES, Detector
from app.event_publisher import EventPublisher
from app.frame_reader import FrameReader
from app.overlay import draw_rules
from app.rule_engine import RuleEngine, RuleHit, RulesFile, object_positions

log = logging.getLogger("vision")
STATUS_REPORT_SECONDS = 5.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", help="RTSP URL or video file (repeatable); disables API mode")
    parser.add_argument("--show", action="store_true", help="show annotated preview windows")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    api = ApiClient(settings.api_base_url, settings.service_token) \
        if settings.api_base_url and not args.source else None
    log.info("Mode: %s", f"API ({settings.api_base_url})" if api else "standalone")

    detector = Detector(
        settings.model_path, settings.inference_device, settings.detection_confidence, settings.classes
    )
    log.info("Model %s, input %spx, provider %s", settings.model_path.name, detector.resolution, detector.provider)

    rules_file = RulesFile(settings.rules_path)
    rules_file.changed()
    publisher = EventPublisher(settings.event_storage_path)
    cameras: dict[int, dict] = {}

    def wanted() -> dict[int, str] | None:
        """camera id -> source. None means "couldn't tell", so keep what's running."""
        if args.source:
            return dict(enumerate(args.source, start=1))
        if api is None:
            return dict(enumerate(settings.sources, start=1))
        try:
            return {c["id"]: c["stream_url"] for c in api.cameras() if c["enabled"]}
        except httpx.HTTPError as exc:
            log.warning("Could not fetch cameras from API (%s); keeping current set", exc)
            return None

    def sync_cameras() -> None:
        desired = wanted()
        if desired is None:
            return
        for cam_id in list(cameras):
            if desired.get(cam_id) != cameras[cam_id]["source"]:
                stop_camera(cameras.pop(cam_id), args.show)
                log.info("cam%d stopped", cam_id)
        for cam_id, source in desired.items():
            if cam_id not in cameras:
                cameras[cam_id] = start_camera(cam_id, source, rules_file)

    def emit(cam: dict, hit: RuleHit, image) -> None:
        event = publisher.publish(cam["key"], hit, image, cam["engine"].rules)
        if api:
            api.send_event({**event, "camera_id": cam["id"]})

    box_annotator = sv.BoxAnnotator()
    label_annotator = sv.LabelAnnotator(text_scale=0.5)
    interval = 1.0 / settings.inference_fps
    next_rules_check = next_camera_sync = next_status_report = 0.0

    try:
        while True:
            now = time.monotonic()
            if now >= next_camera_sync:
                next_camera_sync = now + (settings.camera_refresh_seconds if api else 1e9)
                sync_cameras()
            if now >= next_rules_check:
                next_rules_check = now + 2.0
                if rules_file.changed():
                    for cam in cameras.values():
                        cam["engine"].set_rules(rules_file.for_camera(cam["key"]))
            if api and now >= next_status_report:
                next_status_report = now + STATUS_REPORT_SECONDS
                for cam in cameras.values():
                    reader = cam["reader"]
                    last = reader.last_frame_at
                    api.report_status(cam["id"], reader.status,
                                      datetime.fromtimestamp(last, timezone.utc).isoformat() if last else None,
                                      cam["fps"] if reader.status == "online" else None)

            for cam in cameras.values():
                check_health(cam, emit)
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
                    emit(cam, hit, frame.image)

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
                    cv2.imshow(cam["key"], img)

                if now - stats["last_report"] >= 1.0:
                    counts: dict[str, int] = {}
                    for name in names:
                        counts[name] = counts.get(name, 0) + 1
                    elapsed = now - stats["last_report"]
                    cam["fps"] = round(stats["inferences"] / elapsed, 2)
                    print(json.dumps({
                        "camera": cam["key"],
                        "status": cam["reader"].status,
                        "infer_fps": cam["fps"],
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
        for cam in cameras.values():
            stop_camera(cam, args.show)
        if api:
            api.close()
        if args.show:
            cv2.destroyAllWindows()


def start_camera(cam_id: int, source: str, rules_file: RulesFile) -> dict:
    key = f"cam{cam_id}"
    rules = rules_file.for_camera(key)
    log.info("%s starting (%d rules)", key, len(rules))  # never log the source: it may contain a password
    return {
        "id": cam_id,
        "key": key,
        "source": source,
        "reader": FrameReader(key, source).start(),
        # frame_rate = our sampling rate, so lost_track_buffer is measured in sampled frames
        "tracker": sv.ByteTrack(frame_rate=max(1, round(settings.inference_fps))),
        "engine": RuleEngine(rules),
        "started_at": time.time(),
        "offline_alerted": False,
        "last_image": None,
        "fps": None,
        "next_due": 0.0,
        "stats": {"inferences": 0, "infer_ms": 0.0, "last_report": time.monotonic()},
    }


def stop_camera(cam: dict, show: bool) -> None:
    cam["reader"].stop()
    if show:
        try:
            cv2.destroyWindow(cam["key"])
        except cv2.error:
            pass


def check_health(cam: dict, emit) -> None:
    """Raise camera_offline after a sustained outage, and camera_online when it recovers."""
    reader = cam["reader"]
    wall = time.time()
    if reader.status == "online":
        if cam["offline_alerted"]:
            cam["offline_alerted"] = False
            emit(cam, RuleHit(rule=None, event_type="camera_online", occurred_at=wall), None)
        return
    last_ok = reader.last_frame_at or cam["started_at"]
    down_for = wall - last_ok
    if not cam["offline_alerted"] and down_for >= settings.offline_alert_seconds:
        cam["offline_alerted"] = True
        hit = RuleHit(rule=None, event_type="camera_offline", occurred_at=wall,
                      details={"offline_seconds": round(down_for, 1), "reconnects": reader.reconnects})
        # Last good frame (if any) shows what the camera saw before it went down.
        emit(cam, hit, cam["last_image"])


if __name__ == "__main__":
    main()
