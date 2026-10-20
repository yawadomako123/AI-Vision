"""Multi-camera endurance test: how many cameras can this machine handle, and does it stay up?

Publishes N looping fake cameras to MediaMTX, adds them as temporary cameras with one zone
each, and samples every 30 s for as long as you like:
- per camera: online/offline, frames analysed per second, alerts;
- the vision worker's memory and CPU;
- alert delay (frame seen -> alert stored by the API).

Writes a Markdown report to storage/soak-<date>.md and removes its cameras at the end.
The clip is converted to H.264 once and then streamed without re-encoding, so the fake
cameras themselves cost almost no CPU (real cameras encode on their own hardware).

Run from services/backend with the API and a worker running (scripts/supervise.ps1):
    uv run python ../../scripts/soak-test.py --cameras 2 --minutes 30
    uv run python ../../scripts/soak-test.py --cameras 4 --minutes 240
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e import API, ENV, ROOT, kill_tree, session, wait_for  # noqa: E402

PREFIX = "Soak test camera"
RTSP = "127.0.0.1:8554"


def h264_clip(src: Path) -> Path:
    out = ROOT / "storage" / f"soak-{src.stem}.mp4"
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-an", "-c:v", "libx264", "-preset", "veryfast",
                        "-g", "15", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def publish(clip: Path, path: str, log) -> subprocess.Popen:
    return subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1", "-i", str(clip), "-an", "-c", "copy",
         "-f", "rtsp", "-rtsp_transport", "tcp", f"rtsp://publisher:changeme-publish@{RTSP}/{path}"],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log)


def worker_stats() -> dict | None:
    """Memory (MB) and total CPU seconds of the vision worker's Python process (Windows)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -match ' -m app\\.main' -and $_.CommandLine -notmatch '\\\\.venv\\\\' } | "
          "Select-Object ProcessId, WorkingSetSize, KernelModeTime, UserModeTime | ConvertTo-Json")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout
        rows = json.loads(out) if out.strip() else []
    except (subprocess.SubprocessError, ValueError):
        return None
    rows = rows if isinstance(rows, list) else [rows]
    if not rows:
        return None
    r = max(rows, key=lambda r: r["WorkingSetSize"])
    return {"pid": r["ProcessId"], "mb": r["WorkingSetSize"] / 1024**2,
            "cpu_s": (r["KernelModeTime"] + r["UserModeTime"]) / 1e7}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cameras", type=int, default=2)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--clip", type=Path, default=ROOT / "samples" / "e2e-person.mp4")
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()

    admin = session(ENV["E2E_ADMIN_USERNAME"], ENV["E2E_ADMIN_PASSWORD"])
    if not admin.get("/api/system").json()["worker"]["connected"]:
        print("No vision worker is connected. Start everything with scripts/supervise.ps1 first.")
        return 2
    for cam in admin.get("/api/cameras").json():
        if cam["name"].startswith(PREFIX):
            admin.delete(f"/api/cameras/{cam['id']}")

    clip = h264_clip(args.clip)
    log = open(ROOT / "storage" / "soak.log", "w", encoding="utf-8")
    publishers, cams = [], []
    started_at = datetime.now()
    cpu_count = __import__("os").cpu_count() or 1
    samples: list[dict] = []
    try:
        for i in range(1, args.cameras + 1):
            publishers.append(publish(clip, f"soak{i}", log))
            cam = admin.post("/api/cameras", json={"name": f"{PREFIX} {i}", "location": "soak test",
                                                   "stream_url": f"rtsp://{RTSP}/soak{i}"}).json()
            admin.post(f"/api/cameras/{cam['id']}/rules", json={
                "type": "zone_entry", "name": "Soak zone", "severity": "low", "cooldown_seconds": 10,
                "points": [[0.6, 0.05], [0.99, 0.05], [0.99, 0.99], [0.6, 0.99]]})
            cams.append(cam["id"])
        if not wait_for(lambda: all(admin.get(f"/api/cameras/{c}").json()["status"] == "online" for c in cams), 120):
            print("  warning: not all cameras came online within 2 min")

        print(f"\n  Soak test: {args.cameras} camera(s) for {args.minutes:g} min, sampling every {args.interval:g} s\n")
        print(f"  {'time':>6}  {'online':>6}  {'fps per camera':<28} {'RAM MB':>7} {'CPU %':>6}  alerts")
        end = time.monotonic() + args.minutes * 60
        prev = worker_stats()
        prev_t = time.monotonic()
        while time.monotonic() < end:
            time.sleep(min(args.interval, max(1, end - time.monotonic())))
            now_t = time.monotonic()
            info = [admin.get(f"/api/cameras/{c}").json() for c in cams]
            stats = worker_stats()
            cpu = None
            if stats and prev and stats["pid"] == prev["pid"]:
                cpu = (stats["cpu_s"] - prev["cpu_s"]) / (now_t - prev_t) / cpu_count * 100
            restarted = bool(stats and prev and stats["pid"] != prev["pid"])
            prev, prev_t = stats, now_t
            alerts = sum(admin.get("/api/events", params={"camera_id": c, "limit": 1}).json()["total"] for c in cams)
            s = {"t": (args.minutes * 60 - (end - now_t)) / 60, "online": sum(c["status"] == "online" for c in info),
                 "fps": [c.get("infer_fps") or 0 for c in info], "mb": stats["mb"] if stats else None, "cpu": cpu,
                 "alerts": alerts, "worker_restarted": restarted}
            samples.append(s)
            fps = " ".join(f"{f:4.1f}" for f in s["fps"])
            print(f"  {s['t']:5.1f}m  {s['online']:>3}/{len(cams):<2}  {fps:<28} "
                  f"{(s['mb'] or 0):7.0f} {(cpu if cpu is not None else 0):6.1f}  {alerts}"
                  + ("  (worker restarted!)" if restarted else ""))
    except KeyboardInterrupt:
        print("\n  stopped early")
    finally:
        delays = []
        offline_events = 0
        for c in cams:
            try:
                for e in admin.get("/api/events", params={"camera_id": c, "limit": 200}).json().get("items", []):
                    if e["event_type"] == "camera_offline":
                        offline_events += 1
                    elif e.get("received_at"):
                        delays.append((datetime.fromisoformat(e["received_at"])
                                       - datetime.fromisoformat(e["occurred_at"])).total_seconds())
            finally:
                admin.delete(f"/api/cameras/{c}")  # always clean up, even if reading the results failed
        for p in publishers:
            kill_tree(p)

    if not samples:
        return 1
    online = sum(s["online"] for s in samples) / (len(samples) * len(cams)) * 100
    all_fps = [f for s in samples for f in s["fps"] if f]
    mbs = [s["mb"] for s in samples if s["mb"]]
    cpus = [s["cpu"] for s in samples if s["cpu"] is not None]
    pct = lambda xs, q: sorted(xs)[min(len(xs) - 1, int(q * len(xs)))] if xs else None
    lines = [
        f"# Soak test {started_at:%Y-%m-%d %H:%M}",
        "",
        f"- Cameras: {len(cams)} (looping {args.clip.name}), duration {samples[-1]['t']:.1f} min, {len(samples)} samples",
        f"- Online: {online:.1f}% of checks; camera_offline alerts: {offline_events}",
        f"- Analysis speed per camera: avg {statistics.mean(all_fps):.2f} fps, min {min(all_fps):.2f}" if all_fps else "- Analysis speed: no data",
        f"- Worker memory: start {mbs[0]:.0f} MB, end {mbs[-1]:.0f} MB, max {max(mbs):.0f} MB" if mbs else "- Worker memory: not measured",
        f"- Worker CPU (share of all cores): avg {statistics.mean(cpus):.1f}%, max {max(cpus):.1f}%" if cpus else "- Worker CPU: not measured",
        f"- Worker restarts during the test: {sum(s['worker_restarted'] for s in samples)}",
        f"- Alerts: {samples[-1]['alerts']}; alert delay p50 {pct(delays, 0.5):.2f} s, p95 {pct(delays, 0.95):.2f} s" if delays
        else f"- Alerts: {samples[-1]['alerts']}",
        "",
        "| min | online | fps per camera | RAM MB | CPU % | alerts |",
        "|---|---|---|---|---|---|",
    ] + [f"| {s['t']:.1f} | {s['online']}/{len(cams)} | {' / '.join(f'{f:.1f}' for f in s['fps'])} | "
         f"{(s['mb'] or 0):.0f} | {(s['cpu'] or 0):.1f} | {s['alerts']} |" for s in samples]
    report = ROOT / "storage" / f"soak-{started_at:%Y%m%d-%H%M}-{len(cams)}cam.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines[:9]) + f"\n\n  Report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
