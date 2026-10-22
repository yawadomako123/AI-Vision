"""Vision worker: ingest -> detect -> track -> rules -> events, for every camera.

    uv run python -m app.main                      # cameras from the API (API_BASE_URL) or CAMERA_SOURCES
    uv run python -m app.main --show               # also open preview windows
    uv run python -m app.main --source ../../samples/door.mp4 --show   # one-off file, no API

API mode (API_BASE_URL set): cameras are fetched from the API every CAMERA_REFRESH_SECONDS
(added/removed/changed cameras are picked up live), camera status is reported every 5 s and
events are sent to the API. Events and snapshots are always written to storage/events too.

Rules are drawn in the dashboard and fetched every few seconds, together with runtime settings
(clips, search on/off, analysis speed) which apply without a restart. Standalone mode (no API)
only detects and tracks: useful for benchmarks and quick tests on a video file.
"""

import argparse
import warnings
import json
import logging
import time
from datetime import datetime, timezone

import cv2
import httpx
import supervision as sv

from app.api_client import ApiClient
from app.clips import ClipConfig, ClipRecorder
from app.config import REPO_ROOT, settings
from app.detector import COCO_CLASSES, Detector
from app.event_publisher import EventPublisher
from app.frame_reader import FrameReader
from app.live import LivePublisher, live_payload
from app.motion import MotionGate
from app.overlay import draw_rules
from app.counts import LineCounter, MinuteCounter
from app.rule_engine import Rule, RuleEngine, RuleHit, object_positions
from app.sightings import SightingCollector, SiglipEmbedder

log = logging.getLogger("vision")
# supervision is pinned below 0.31 (pyproject.toml), where ByteTrack still exists; its
# deprecation notice is just noise in the logs until we move to the `trackers` package.
warnings.filterwarnings("ignore", message=".*ByteTrack.*deprecated", category=FutureWarning)
STATUS_REPORT_SECONDS = 5.0
RULES_REFRESH_SECONDS = 3.0


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

    api_rules: dict[int, list[Rule]] = {}
    clip_config = ClipConfig(enabled=settings.clips_enabled)
    cameras: dict[int, dict] = {}
    # Runtime settings from the dashboard (Settings page); defaults until the API answers.
    runtime = {"fps": settings.inference_fps, "search": False, "live": set(), "motion_skip": True}
    live = LivePublisher(settings.api_base_url, settings.service_token) if api else None
    embedder: SiglipEmbedder | None = None

    def apply_config(cfg: dict) -> None:
        nonlocal clip_config, embedder
        new_clips = ClipConfig(**{k: v for k, v in cfg.get("clips", {}).items() if k in ClipConfig.__dataclass_fields__})
        if new_clips != clip_config:
            clip_config = new_clips
            for cam in cameras.values():
                cam["clips"].configure(clip_config)
        fps = float(cfg.get("analysis_fps") or settings.inference_fps)
        if fps != runtime["fps"]:
            runtime["fps"] = fps
            for cam in cameras.values():  # tracker timing depends on the analysis rate
                cam["tracker"] = new_tracker(fps)
            log.info("Analysis speed: %.1f frames/s per camera", fps)
        runtime["live"] = {int(c) for c in cfg.get("live_cameras", [])}
        if bool(cfg.get("motion_skip", True)) != runtime["motion_skip"]:
            runtime["motion_skip"] = bool(cfg.get("motion_skip", True))
            log.info("Motion skip %s", "on" if runtime["motion_skip"] else "off")
        conf = cfg.get("detection_confidence")
        if conf and abs(float(conf) - detector.confidence) > 1e-6:
            detector.confidence = float(conf)
            log.info("Detection sensitivity: minimum confidence %.2f", detector.confidence)
        want_search = bool(cfg.get("search_enabled"))
        if want_search and embedder is None:
            if (settings.search_model_dir / "vision.onnx").exists():
                try:
                    embedder = SiglipEmbedder(settings.search_model_dir, settings.search_device)
                    log.info("Search on: sightings recorded (%s)", embedder.provider)
                except Exception:  # a broken model must not stop detection
                    log.exception("Could not load the search model; search stays off")
            else:
                log.warning("Search is switched on but the model isn't installed (scripts/export_siglip.py)")
        elif not want_search and embedder is not None:
            embedder = None  # frees the model's memory
            log.info("Search off: sightings not recorded")
        runtime["search"] = embedder is not None
        for cam in cameras.values():
            cam["sightings"].embedder = embedder

    def fetch_api_rules() -> bool:
        """Refresh api_rules and runtime settings. Returns False if the API couldn't be reached."""
        try:
            raw = api.rules()
            apply_config(api.config())
        except httpx.HTTPError as exc:
            log.warning("Could not fetch rules from API (%s); keeping current rules", exc)
            return False
        parsed: dict[int, list[Rule]] = {}
        for cam_id, rules in raw.items():
            for r in rules:
                try:
                    parsed.setdefault(cam_id, []).append(Rule.from_dict({**r, "id": f"rule{r['id']}"}))
                except (KeyError, ValueError) as exc:
                    log.error("Skipping invalid rule %s: %s", r.get("id"), exc)
        api_rules.clear()
        api_rules.update(parsed)
        return True

    def rules_for(cam_id: int) -> list[Rule]:
        return api_rules.get(cam_id, [])

    def apply_rules() -> None:
        for cam in cameras.values():
            new = rules_for(cam["id"])
            if [vars(r) for r in new] != [vars(r) for r in cam["engine"].rules]:  # don't reset state needlessly
                cam["engine"].set_rules(new)
                log.info("%s rules updated (%d)", cam["key"], len(new))

    if api:
        fetch_api_rules()
    publisher = EventPublisher(settings.event_storage_path)

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
                cameras[cam_id] = start_camera(cam_id, source, rules_for(cam_id), clip_config, runtime["fps"])
                cameras[cam_id]["sightings"] = SightingCollector(cam_id, api.send_sighting if api else None,
                                                                 embedder if api else None)

    def emit(cam: dict, hit: RuleHit, image) -> None:
        event = publisher.publish(cam["key"], hit, image, cam["engine"].rules, clips=cam["clips"])
        if api:
            api.send_event({**event, "camera_id": cam["id"]})

    box_annotator = sv.BoxAnnotator()
    label_annotator = sv.LabelAnnotator(text_scale=0.5)
    next_rules_check = next_camera_sync = next_status_report = next_heartbeat = 0.0
    # scripts/supervise.ps1 restarts the worker if this file stops being refreshed (a hang).
    heartbeat = REPO_ROOT / "storage" / "worker.heartbeat"

    try:
        while True:
            now = time.monotonic()
            if now >= next_camera_sync:
                next_camera_sync = now + (settings.camera_refresh_seconds if api else 1e9)
                sync_cameras()
            if now >= next_rules_check:
                next_rules_check = now + RULES_REFRESH_SECONDS
                if api and fetch_api_rules():
                    apply_rules()
            if now >= next_heartbeat:
                next_heartbeat = now + 5
                try:
                    heartbeat.write_text(str(time.time()), encoding="ascii")
                except OSError:
                    pass
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
                cam["clips"].tick(time.time())
                if "sightings" in cam:
                    cam["sightings"].tick(time.time())
                if now < cam["next_due"]:
                    continue
                frame = cam["reader"].latest(timeout=0)
                if frame is None:
                    continue
                cam["next_due"] = now + 1.0 / runtime["fps"]
                cam["last_image"] = frame.image
                stats = cam["stats"]

                if runtime["motion_skip"] and not cam["motion"].should_analyse(frame.image, now, cam["tracking"]):
                    stats["skipped"] += 1
                    # Nothing moving: keep the live wall's picture fresh without running the AI.
                    if live and cam["id"] in runtime["live"] and live.wants_preview(cam["id"], now):
                        h, w = frame.image.shape[:2]
                        live.offer(live_payload(cam["id"], sv.Detections.empty(), [], w, h, frame.captured_at, frame.image))
                    continue

                t0 = time.perf_counter()
                detections = cam["tracker"].update_with_detections(detector(frame.image))
                infer_ms = (time.perf_counter() - t0) * 1000

                h, w = frame.image.shape[:2]
                engine = cam["engine"]
                for hit in engine.update(detections, w, h, frame.captured_at):
                    emit(cam, hit, frame.image)

                # Derived from class_id: the tracker drops `data` when nothing is detected.
                names = [COCO_CLASSES.get(int(c), str(c)) for c in detections.class_id]
                cam["sightings"].update(detections, names, object_positions(detections, h).xyxy[:, :2],
                                        frame.image, frame.captured_at)
                if live and cam["id"] in runtime["live"]:
                    preview = frame.image if live.wants_preview(cam["id"], now) else None
                    live.offer(live_payload(cam["id"], detections, names, w, h, frame.captured_at, preview))
                samples = cam["counter"].add(names, frame.captured_at)
                line_samples = cam["lines"].add(engine.take_crossings(), frame.captured_at)
                if api:
                    api.send_counts(samples)
                    api.send_line_counts(line_samples)
                cam["tracking"] = len(detections) > 0
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
                        "idle": cam["motion"].idle,  # motion skip: nothing moving, AI resting
                        "skipped": stats["skipped"],
                        "frame_age_ms": round((time.time() - frame.captured_at) * 1000),
                        "objects": counts,
                        "track_ids": [int(t) for t in detections.tracker_id],
                    }), flush=True)
                    stats.update(inferences=0, skipped=0, infer_ms=0.0, last_report=now)

            if args.show and cv2.waitKey(1) & 0xFF == ord("q"):
                break
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        for cam in cameras.values():
            stop_camera(cam, args.show)
            if api:
                api.send_counts(cam["counter"].flush())  # partial last minute
                api.send_line_counts(cam["lines"].flush())
        if live:
            live.close()
        if api:
            api.close()
        if args.show:
            cv2.destroyAllWindows()


def new_tracker(fps: float) -> sv.ByteTrack:
    """frame_rate = our sampling rate, so lost_track_buffer is measured in sampled frames.
    A person only counts (and can trigger rules) once seen in 2 analysed frames in a row: that
    filters one-frame ghost boxes, which made 2 people show as 3 or 4, for ~0.3 s of delay."""
    return sv.ByteTrack(frame_rate=max(1, round(fps)), minimum_consecutive_frames=2)


def start_camera(cam_id: int, source: str, rules: list[Rule], clip_config: ClipConfig, fps: float) -> dict:
    key = f"cam{cam_id}"
    log.info("%s starting (%d rules)", key, len(rules))  # never log the source: it may contain a password
    clips = ClipRecorder(clip_config)
    reader = FrameReader(key, source)
    reader.on_frame = clips.add_frame  # pre-roll for evidence clips
    return {
        "id": cam_id,
        "key": key,
        "source": source,
        "clips": clips,
        "reader": reader.start(),
        "tracker": new_tracker(fps),
        "engine": RuleEngine(rules),
        "counter": MinuteCounter(cam_id),
        "lines": LineCounter(cam_id),
        "started_at": time.time(),
        "offline_alerted": False,
        "last_image": None,
        "fps": None,
        "next_due": 0.0,
        "stats": {"inferences": 0, "skipped": 0, "infer_ms": 0.0, "last_report": time.monotonic()},
        "motion": MotionGate(),
        "tracking": False,
    }


def stop_camera(cam: dict, show: bool) -> None:
    cam["reader"].stop()
    cam["clips"].flush()
    if "sightings" in cam:
        cam["sightings"].flush()
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
