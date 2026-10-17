"""End-to-end test of the whole system: camera stream -> worker -> API -> live updates.

Needs: MediaMTX + PostgreSQL (docker compose up -d), the API running on 127.0.0.1:8000,
the e2e clip (scripts/make_e2e_clip.py) and E2E_ADMIN_USERNAME / E2E_ADMIN_PASSWORD in .env.
Starts its own vision worker unless one is already connected to the API.

Run from services/backend:
    uv run python ../../scripts/e2e.py            # cleans up after itself
    uv run python ../../scripts/e2e.py --keep     # leave the test camera + events to look at

Against the Docker deployment:  E2E_API=http://127.0.0.1:8080 E2E_CAMERA_RTSP=mediamtx:8554
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("E2E_API", "http://127.0.0.1:8000")
RTSP = "127.0.0.1:8554"  # where this script publishes the test stream
# How the worker reaches MediaMTX: 127.0.0.1 natively, "mediamtx" inside Docker Compose.
CAMERA_RTSP = os.environ.get("E2E_CAMERA_RTSP", RTSP)
PATH = "e2e"
CAMERA_NAME = "E2E test camera"
OPERATOR = "e2e-operator"
CLIP = ROOT / "samples" / "e2e-person.mp4"
(ROOT / "storage").mkdir(exist_ok=True)
LOG = open(ROOT / "storage" / "e2e.log", "w", encoding="utf-8", buffering=1)  # git-ignored


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}", flush=True)
    return ok


def wait_for(fn, timeout: float, interval: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


class LiveListener(threading.Thread):
    """Collects WebSocket pushes the way the dashboard receives them."""

    def __init__(self, cookie: str):
        super().__init__(daemon=True)
        self.cookie = cookie
        self.messages: list[dict] = []
        self.connected = threading.Event()
        self.stop = False

    def run(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        async with websockets.connect(API.replace("http", "ws") + "/api/ws",
                                      additional_headers={"Cookie": self.cookie}) as ws:
            self.connected.set()
            while not self.stop:
                try:
                    self.messages.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=1)))
                except asyncio.TimeoutError:
                    continue


class Client(httpx.Client):
    """Retries once when the server closed an idle keep-alive connection we tried to reuse."""

    def request(self, *args, **kwargs):
        try:
            return super().request(*args, **kwargs)
        except httpx.RemoteProtocolError:
            return super().request(*args, **kwargs)


def session(username: str, password: str) -> httpx.Client:
    c = Client(base_url=API, headers={"X-Requested-With": "aivision"}, timeout=20)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    r.raise_for_status()
    return c


def kill_tree(proc: subprocess.Popen) -> None:
    """`uv run` spawns python as a child; terminate() alone would orphan it on Windows."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
    proc.wait(10)


def start_publisher() -> subprocess.Popen:
    return subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1", "-i", str(CLIP),
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-g", "15",
         "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp",
         f"rtsp://publisher:changeme-publish@{RTSP}/{PATH}"],
        stdin=subprocess.DEVNULL, stdout=LOG, stderr=LOG,
    )


def main() -> int:
    print("AI Vision Edge end-to-end test\n")
    if not CLIP.exists():
        print(f"Missing {CLIP}. Create it with scripts/make_e2e_clip.py first.")
        return 2
    try:
        httpx.get(f"{API}/health", timeout=5).raise_for_status()
    except httpx.HTTPError as exc:
        print(f"API not reachable at {API}: {exc}")
        return 2

    admin = session(ENV["E2E_ADMIN_USERNAME"], ENV["E2E_ADMIN_PASSWORD"])
    check("Admin can sign in", admin.get("/api/auth/me").json()["role"] == "admin")
    check("API rejects anonymous requests", httpx.get(f"{API}/api/events").status_code == 401)

    # Clean slate: remove a camera left over from an interrupted run.
    for cam in admin.get("/api/cameras").json():
        if cam["name"] == CAMERA_NAME:
            admin.delete(f"/api/cameras/{cam['id']}")

    live = LiveListener("; ".join(f"{k}={v}" for k, v in admin.cookies.items()))
    live.start()
    check("Live updates connect (WebSocket)", live.connected.wait(10))

    was_armed = admin.get("/api/arming").json()["armed"]
    admin.put("/api/arming", json={"armed": True})

    # Notifications: only against a server we control (never publish test alerts to ntfy.sh).
    ntfy_server = os.environ.get("E2E_NTFY_SERVER")
    # Always save and restore the real settings: test alerts must never reach the owner's phone.
    saved_notifications = admin.get("/api/settings/notifications").json()
    ntfy_topic = f"aivision-e2e-{os.urandom(6).hex()}"
    if not ntfy_server:
        admin.patch("/api/settings/notifications", json={"enabled": False})
    else:
        admin.patch("/api/settings/notifications", json={
            "enabled": True, "server": ntfy_server, "topic": ntfy_topic, "min_severity": "high",
            "include_snapshot": True, "cooldown_seconds": 0})
        test = admin.post("/api/settings/notifications/test").json()
        check("Notification test message is delivered", test["ok"], test.get("error") or "")

    camera = admin.post("/api/cameras", json={"name": CAMERA_NAME, "location": "e2e",
                                              "stream_url": f"rtsp://{CAMERA_RTSP}/{PATH}"}).json()
    cam_id = camera["id"]
    zone = admin.post(f"/api/cameras/{cam_id}/rules", json={
        "type": "zone_entry", "name": "E2E zone", "severity": "high",
        "points": [[0.6, 0.05], [0.99, 0.05], [0.99, 0.99], [0.6, 0.99]]}).json()
    line = admin.post(f"/api/cameras/{cam_id}/rules", json={
        "type": "line_crossing", "name": "E2E line", "points": [[0.45, 0.02], [0.45, 0.98]]}).json()
    check("Camera and rules created", "id" in zone and "id" in line)

    worker = None
    if admin.get("/api/system").json()["worker"]["connected"]:
        print("  (using the vision worker that is already running)")
    else:
        print("  (starting a vision worker)")
        worker = subprocess.Popen(["uv", "run", "python", "-m", "app.main"], cwd=ROOT / "services" / "vision",
                                  env={**os.environ, "PYTHONUNBUFFERED": "1"},
                                  stdout=subprocess.DEVNULL, stderr=LOG, stdin=subprocess.DEVNULL)

        # Loading the model can take minutes on a busy machine; don't start the clock until then.
        ready = wait_for(lambda: admin.get("/api/system").json()["worker"]["connected"], 300, interval=3)
        check("Vision worker starts and connects to the API", bool(ready))

    publisher = start_publisher()
    print(f"  (worker and stream logs: {LOG.name})")
    started = time.monotonic()
    try:
        online = wait_for(lambda: admin.get(f"/api/cameras/{cam_id}").json()["status"] == "online", 120)
        check("Worker picks up the new camera and reports it online", bool(online),
              f"after {time.monotonic() - started:.0f} s")

        def rule_events():
            items = admin.get("/api/events", params={"camera_id": cam_id, "limit": 50}).json()["items"]
            types = {e["event_type"] for e in items}
            return items if {"zone_entry", "line_crossing"} <= types else None

        events = wait_for(rule_events, 120) or admin.get("/api/events", params={"camera_id": cam_id}).json()["items"]
        types = {e["event_type"] for e in events}
        check("Person walking into the zone raises zone_entry", "zone_entry" in types)
        check("Person crossing the line raises line_crossing", "line_crossing" in types)

        entry = next((e for e in events if e["event_type"] == "zone_entry"), None)
        if entry:
            check("Event has person, track id and confidence",
                  entry["object_class"] == "person" and entry["track_id"] is not None and entry["confidence"] > 0.4,
                  f"confidence {entry['confidence']}")
            snap = admin.get(entry["snapshot_url"]) if entry["snapshot_url"] else None
            check("Snapshot is served to signed-in users",
                  bool(snap) and snap.status_code == 200 and snap.headers["content-type"] == "image/jpeg",
                  f"{len(snap.content)} bytes" if snap else "no snapshot")
            check("Snapshot is refused without a session",
                  httpx.get(API + entry["snapshot_url"]).status_code == 401 if entry["snapshot_url"] else False)
            if entry["clip_url"]:
                def clip_ready():
                    r = admin.get(entry["clip_url"])
                    return r if r.status_code == 200 else None
                clip = wait_for(clip_ready, 45, interval=3)
                detail = "not produced"
                ok = False
                if clip:
                    import io

                    import av
                    with av.open(io.BytesIO(clip.content)) as c:
                        v = c.streams.video[0]
                        frames = sum(1 for _ in c.decode(v))
                        seconds = frames / float(v.average_rate or 6)
                    kb = len(clip.content) / 1024
                    ok = clip.headers["content-type"] == "video/mp4" and kb < 1024 and seconds >= 5
                    detail = f"{seconds:.0f} s, {v.width}x{v.height}, {kb:.0f} KB"
                check("Alert has a short, small video clip", ok, detail)
                check("Clip is refused without a session", httpx.get(API + entry["clip_url"]).status_code == 401)
            else:
                check("Alert has a short, small video clip", False, "no clip_url on the event")

            pushed = [m for m in live.messages if m["type"] == "event" and m["data"]["id"] == entry["id"]]
            check("Event was pushed live to the dashboard", bool(pushed))

            if ntfy_server:
                def pushed_to_phone():
                    r = httpx.get(f"{ntfy_server}/{ntfy_topic}/json", params={"poll": "1", "since": "all"}, timeout=10)
                    msgs = [json.loads(line) for line in r.text.splitlines() if line.strip()]
                    return [m for m in msgs if "Zone entry" in m.get("title", "")]
                pushed = wait_for(pushed_to_phone, 30)
                check("Zone alert is pushed to the phone (ntfy)", bool(pushed),
                      "with snapshot attached" if pushed and pushed[0].get("attachment") else "no attachment")

            reviewed = admin.patch(f"/api/events/{entry['id']}", json={"status": "resolved", "note": "e2e"}).json()
            check("Event can be resolved; reviewer recorded from session",
                  reviewed["status"] == "resolved" and reviewed["reviewed_by"] == ENV["E2E_ADMIN_USERNAME"])

        def counted():
            ts = admin.get("/api/analytics/timeseries", params={"hours": 24, "camera_id": cam_id}).json()
            return max(ts["counts"].get("person", {}).get("peak", [0]))
        peak = wait_for(counted, 100, interval=5)
        check("People counts reach analytics", bool(peak), f"peak {peak} person(s) in view")

        csv = admin.get("/api/events/export.csv", params={"camera_id": cam_id})
        check("CSV export includes the events", csv.status_code == 200 and "E2E zone" in csv.text)

        # Disarm: with the person still walking through, rule alerts must stop; re-arming resumes them.
        def rule_event_count() -> int:
            items = admin.get("/api/events", params={"camera_id": cam_id, "limit": 200}).json()["items"]
            return sum(e["event_type"] in ("zone_entry", "line_crossing") for e in items)

        admin.put("/api/arming", json={"armed": False})
        time.sleep(8)  # worker refreshes rules every 3 s; let in-flight events land
        before = rule_event_count()
        time.sleep(25)  # the clip loops every 16 s, so the person crosses again in this window
        check("Disarmed: no rule alerts while someone walks through", rule_event_count() == before)
        admin.put("/api/arming", json={"armed": True})
        resumed = wait_for(lambda: rule_event_count() > before, 60)
        check("Re-armed: rule alerts resume", bool(resumed))

        # Operator: can review, cannot administer.
        users = {u["username"]: u for u in admin.get("/api/users").json()}
        op_password = ENV["E2E_ADMIN_PASSWORD"] + "-op"
        if OPERATOR in users:
            admin.patch(f"/api/users/{users[OPERATOR]['id']}", json={"active": True, "password": op_password, "role": "operator"})
        else:
            admin.post("/api/users", json={"username": OPERATOR, "password": op_password, "role": "operator"})
        op = session(OPERATOR, op_password)
        check("Operator can see events", op.get("/api/events", params={"camera_id": cam_id}).json()["total"] > 0)
        check("Operator cannot change cameras or rules",
              op.patch(f"/api/cameras/{cam_id}", json={"enabled": False}).status_code == 403
              and op.delete(f"/api/rules/{zone['id']}").status_code == 403)
        op.close()

        # Camera goes away -> offline alert.
        kill_tree(publisher)
        offline = wait_for(lambda: any(
            e["event_type"] == "camera_offline"
            for e in admin.get("/api/events", params={"camera_id": cam_id, "event_type": "camera_offline"}).json()["items"]
        ), 90)
        check("Stream loss raises camera_offline", bool(offline))

        actions = {a["action"] for a in admin.get("/api/audit", params={"limit": 200}).json()["items"]}
        check("Audit log recorded the changes",
              {"camera.create", "rule.create", "event.review", "events.export"} <= actions)
    finally:
        kill_tree(publisher)
        admin.put("/api/arming", json={"armed": was_armed})
        if saved_notifications:
            admin.patch("/api/settings/notifications",
                        json={k: v for k, v in saved_notifications.items() if k != "has_token"})
        live.stop = True
        if worker:
            kill_tree(worker)
        if "--keep" in sys.argv:
            print(f"  (--keep: left camera {cam_id} and its events in place for inspection)")
        else:
            admin.delete(f"/api/cameras/{cam_id}")
        users = {u["username"]: u for u in admin.get("/api/users").json()}
        if OPERATOR in users:
            admin.patch(f"/api/users/{users[OPERATOR]['id']}", json={"active": False})
        admin.close()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed in {time.monotonic() - started:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
