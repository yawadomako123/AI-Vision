"""Run the whole system on a real live stream for a few minutes and report how it did.

Adds a temporary camera with a zone (middle of the image) and a counter line, starts a vision
worker if none is connected, then prints status every 30 s: camera online, analysis speed,
alerts, people seen and entries/exits. Removes the camera at the end unless --keep.

Run from services/backend (needs the API running, like scripts/e2e.py):
    uv run python ../../scripts/live-test.py rtsp://user:pass@192.168.1.64/Streaming/Channels/102
    uv run python ../../scripts/live-test.py https://example.org/live/cam.m3u8 --minutes 10
    uv run python ../../scripts/live-test.py "https://www.youtube.com/watch?v=<live id>"   # public live webcams

YouTube links are turned into a plain HLS address with yt-dlp (fetched on the fly with uvx);
those addresses expire after a few hours, so they suit tests, not permanent cameras.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e import API, ENV, ROOT, kill_tree, session, wait_for  # noqa: E402

CAMERA_NAME = "Live test camera"


def resolve(source: str) -> str:
    if "youtube.com/" not in source and "youtu.be/" not in source:
        return source
    print("  (resolving the YouTube live stream with yt-dlp…)")
    out = subprocess.run(
        ["uvx", "yt-dlp", "-q", "--no-warnings", "-g", "-f", "bv*[height<=480][protocol^=m3u8]/b[height<=480]", source],
        capture_output=True, text=True, timeout=180)
    url = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
    if not url.startswith("http"):
        sys.exit(f"Couldn't get a stream from that link: {out.stderr.strip()[-300:]}")
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="RTSP/HTTP(S) stream URL, or a YouTube live link")
    parser.add_argument("--minutes", type=float, default=5)
    parser.add_argument("--keep", action="store_true", help="leave the camera and its events in place")
    args = parser.parse_args()

    try:
        httpx.get(f"{API}/health", timeout=5).raise_for_status()
    except httpx.HTTPError as exc:
        print(f"API not reachable at {API}: {exc}")
        return 2
    url = resolve(args.source)
    admin = session(ENV["E2E_ADMIN_USERNAME"], ENV["E2E_ADMIN_PASSWORD"])
    for cam in admin.get("/api/cameras").json():
        if cam["name"] == CAMERA_NAME:
            admin.delete(f"/api/cameras/{cam['id']}")

    probe = admin.post("/api/cameras/probe", json={"stream_url": url}, timeout=60).json()
    if not probe["ok"]:
        print(f"  Stream check failed: {probe['error']}")
        return 1
    print(f"  Stream OK: {probe['width']}x{probe['height']} {probe['codec']}")

    cam = admin.post("/api/cameras", json={"name": CAMERA_NAME, "location": "live test", "stream_url": url}).json()
    cam_id = cam["id"]
    admin.post(f"/api/cameras/{cam_id}/rules", json={
        "type": "zone_entry", "name": "Live test zone", "severity": "low", "cooldown_seconds": 30,
        "points": [[0.25, 0.25], [0.75, 0.25], [0.75, 0.95], [0.25, 0.95]]})
    admin.post(f"/api/cameras/{cam_id}/rules", json={
        "type": "line_crossing", "alert": False, "name": "Live test counter", "classes": ["person", "car", "bicycle", "bus", "truck"],
        "points": [[0.5, 0.05], [0.5, 0.95]]})
    was_armed = admin.get("/api/arming").json()["armed"]
    admin.put("/api/arming", json={"armed": True})

    worker = None
    if not admin.get("/api/system").json()["worker"]["connected"]:
        print("  (starting a vision worker; loading the model can take a minute)")
        log = open(ROOT / "storage" / "live-test.log", "w", encoding="utf-8")
        worker = subprocess.Popen(["uv", "run", "python", "-m", "app.main"], cwd=ROOT / "services" / "vision",
                                  env={**os.environ, "PYTHONUNBUFFERED": "1"},
                                  stdout=subprocess.DEVNULL, stderr=log, stdin=subprocess.DEVNULL)
        if not wait_for(lambda: admin.get("/api/system").json()["worker"]["connected"], 300, interval=3):
            print("  Worker didn't connect (see storage/live-test.log)")
            kill_tree(worker)
            return 1

    start = time.monotonic()
    end = start + args.minutes * 60
    samples: list[float] = []
    offline_seen = 0
    print(f"\n  Watching for {args.minutes:g} min (Ctrl+C to stop early)\n")
    print(f"  {'time':>5}  {'status':8} {'fps':>4}  {'alerts':>6}  {'people peak':>11}  {'in/out':>9}")
    try:
        while time.monotonic() < end:
            time.sleep(min(30, max(1, end - time.monotonic())))
            c = admin.get(f"/api/cameras/{cam_id}").json()
            fps = c.get("infer_fps") or 0
            if c["status"] == "online":
                samples.append(fps)
            else:
                offline_seen += 1
            alerts = admin.get("/api/events", params={"camera_id": cam_id, "limit": 1}).json()["total"]
            ts = admin.get("/api/analytics/timeseries", params={"hours": 1, "camera_id": cam_id}).json()
            peak = max(ts["counts"].get("person", {}).get("peak", [0]) or [0])
            ent = admin.get("/api/analytics/entries", params={"hours": 1, "camera_id": cam_id}).json()["total"]
            mins = (time.monotonic() - start) / 60
            print(f"  {mins:4.1f}m  {c['status']:8} {fps:4.1f}  {alerts:6}  {peak:11}  {ent['in']:>4}/{ent['out']:<4}")
    except KeyboardInterrupt:
        pass
    finally:
        alerts = admin.get("/api/events", params={"camera_id": cam_id, "limit": 1}).json()["total"]
        admin.put("/api/arming", json={"armed": was_armed})
        if worker:
            kill_tree(worker)
        if args.keep:
            print(f"\n  (--keep: camera {cam_id} left in place; delete it in Cameras when done)")
        else:
            admin.delete(f"/api/cameras/{cam_id}")

    avg = sum(samples) / len(samples) if samples else 0
    print(f"\n  Summary: online in {len(samples)}/{len(samples) + offline_seen} checks, "
          f"avg {avg:.1f} frames/s analysed, {alerts} alert(s)")
    print("  Note: counts reach analytics once per minute, so the last minute may not show yet.")
    return 0 if samples else 1


if __name__ == "__main__":
    sys.exit(main())
