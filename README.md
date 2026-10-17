# AI Vision Edge

Local-first AI video analytics for IP cameras: detect and track people and vehicles, raise alerts from zones and tripwires, and review everything in a live web dashboard. Everything runs on one machine, and every component is free and open source. See [ai-vision-edge-project.md](ai-vision-edge-project.md) for the original spec.

```
Android phone (Larix) ─RTSP─┐
looping test clips ─────────┤─> MediaMTX :8554 ─RTSP─> vision worker ─HTTP─> API :8000 ─> PostgreSQL
real IP cameras ────────────┘        │                (detect, track,         │
                                     │                 rules, counts)          ├─ dashboard (same port)
                                     └──── WebRTC :8889 ── live video ─────────┘  live updates via WebSocket
```

| Piece | Choice | License |
|---|---|---|
| Detector | RF-DETR Nano, exported to ONNX | Apache-2.0 |
| Inference | OpenVINO (Intel iGPU/CPU); ONNX Runtime CPU fallback | Apache-2.0 / MIT |
| Ingest | PyAV (FFmpeg) | BSD |
| Tracking, zones, lines | supervision (ByteTrack, PolygonZone, LineZone) | MIT |
| Stream server | MediaMTX 1.21.2 | MIT |
| API | FastAPI, SQLAlchemy 2, Alembic, argon2 | MIT |
| Database | PostgreSQL 17 | PostgreSQL License |
| Dashboard | Vite, React, TypeScript, Tailwind CSS, TanStack Query | MIT |

## Daily use

```powershell
docker compose up -d                      # MediaMTX + PostgreSQL (if not already running)
.\scripts\run-api.ps1                     # terminal 1: API + dashboard on http://127.0.0.1:8000
cd services\vision; uv run python -m app.main   # terminal 2: vision worker (add --show for preview windows)
```

Open **http://127.0.0.1:8000** and sign in. On the very first run the dashboard asks you to create the admin account.

### Dashboard

| Page | What you do there |
|---|---|
| **Overview** | Cameras online, events in the last 24 h, items needing review, recent alerts |
| **Events** | Filter, open a snapshot, then **Acknowledge**, **Resolve** or mark **False alarm** with a note (your username is recorded) |
| **Cameras** | Add, edit, test, enable or disable, and delete cameras; **Live view**; **Rules** opens the rule editor |
| **Rules** (per camera) | Draw **Zones**, **Tripwires**, **Loitering** and **Occupancy** areas on a current camera image, drag corners to adjust, and set severity, classes, timing and direction. The worker applies changes within 3 s |
| **Analytics** | People and vehicles in view per hour or day, events by type, CSV export (24 h / 7 d / 30 d, per camera) |
| **Settings** | System health, data retention, your password, users and roles (admins), audit log (admins) |

**Arm / Disarm:** the switch at the top of the sidebar. While disarmed, zone, tripwire, loitering and occupancy rules don't raise alerts (for example while you're home). Camera-offline alerts keep working, and every arm and disarm is recorded in the audit log. Operators can arm and disarm too.

**Active hours:** in the rule editor, set a rule to *Only at certain times*, for example weekdays 22:00–06:00. Windows that cross midnight run into the next morning. Times are the edge computer's local time.

**Clips:** each rule alert saves a short clip (about 3 s before to 5 s after, 6 fps, ≤640 px, usually 30–300 KB), shown in the event dialog with a Clip / Snapshot toggle.
- Alerts that fire close together share one clip, and nothing is recorded continuously.
- Clips have their own retention (default 7 days) and a total storage cap (default 1 GB, oldest deleted first).
- Settings → Data retention can turn clips off.

**Roles:** *admins* manage cameras, rules, users and settings. *Operators* watch and review events. New alerts pop up live, and everything updates without reloading.

**Drawing tips:** people are located by their feet (the bottom of their box), so draw zones on the floor. If a person is cut off by the bottom of the image, the middle of the box is used instead.

## First-time setup

```powershell
copy .env.example .env        # then fill in CAMERA_SECRET_KEY / SERVICE_TOKEN (generator commands are in the file)
docker compose up -d
cd services\vision
uv sync
uv run --group export python ..\..\scripts\export_model.py nano    # writes models\rfdetr-nano.onnx
cd ..\backend
uv sync
uv run alembic upgrade head
cd ..\..\apps\web
npm install
npm run build                 # the API serves this build at http://127.0.0.1:8000
```

## Cameras

**Android phone (free, for testing):** install **Larix Broadcaster** and add a connection:
- URL: `rtsp://<laptop Wi-Fi IP>:8554/cam1` (find the IP with `ipconfig`)
- User: `publisher`, password: `changeme-publish`
- Video: 640×480, 15 fps

In the dashboard, add a camera with stream URL `rtsp://127.0.0.1:8554/cam1`. Mount the phone high and angled down, keep it plugged in, and only film places you're allowed to. If the phone can't connect, allow Docker Desktop on *Private* networks in Windows Firewall (TCP 8554).

**Real IP camera:** add it in the dashboard with its RTSP URL (prefer the low-resolution substream), for example `rtsp://user:pass@192.168.1.50:554/stream2`. Passwords are encrypted in the database and never shown again. For live view in the browser, also add the camera as a MediaMTX source (see `infra/mediamtx/mediamtx.yml`) and register `rtsp://127.0.0.1:8554/<path>` instead.

**Test clips without a camera:**

```powershell
.\scripts\test-stream.ps1                                  # color bars on cam1
.\scripts\test-stream.ps1 -Path cam2 -Clip samples\clip.mp4 # loop a recording as cam2
```

## Phone notifications (ntfy, free)

1. Install the free **ntfy** app on your phone.
2. In the dashboard, go to **Settings → Phone notifications**. Click **Generate** for a private topic, tick **Send alerts to my phone**, and click **Save**.
3. In the ntfy app, tap **+** and subscribe to that exact topic (server `ntfy.sh`). Back in the dashboard, click **Send test**.

You'll then get high-severity alerts on your phone, at most one per rule per minute (both are adjustable).

**Privacy:** on the public `ntfy.sh` server, anyone who knows the topic can read the alerts, so keep the generated topic secret. Snapshot images are **not** attached by default. To keep everything, images included, on your own network, self-host ntfy:
- Run `docker compose --profile ntfy up -d`.
- Use server `http://<laptop IP>:2586` in Settings and in the app.
- The phone needs to reach that address: the same Wi-Fi, or Tailscale when you're away.

## Remote access (Tailscale, free)

To open the dashboard on your phone from anywhere, without opening ports to the internet:

1. Install **Tailscale** on the laptop and the phone, and sign in with the same account on both.
2. On the laptop, run `tailscale serve --bg 8000`. It prints an HTTPS address like `https://my-laptop.tail1234.ts.net`.
3. Open that address on your phone. Optionally, paste it as the **Dashboard link** in Settings → Phone notifications, so tapping an alert opens it.

Only devices signed in to your Tailscale account can reach it. The API notices HTTPS and marks the login cookie `Secure` automatically. Over Tailscale everything works except **Live view**: the video stream server isn't exposed, so watch live video on the local network.

## Running a pilot

Before trusting the alerts, run the system for a few days and measure:

1. Mount the camera where it will really be, high and angled at the floor, and draw the rules you'd actually use.
2. Review every alert in **Events**: **Resolve** the real ones, and mark the wrong ones **False alarm**.
3. Check **Analytics → Rule performance**. A false-alarm rate above about 30% (shown in red) means the rule needs tuning:
   - shrink or move its zone
   - raise its **Minimum confidence**
   - lengthen **Repeat alerts after**
   - for loitering, raise the dwell time
4. Repeat until the alert rate matches what really happens.

## Choosing the model (accuracy vs speed)

Measured on this laptop (Iris Xe GPU, with another worker sharing it):

| Model | Input | Time per frame | Person confidence on the test clip | Cameras at 3 fps |
|---|---|---|---|---|
| RF-DETR Nano (default) | 384 px | ~190 ms | 95.0% | ~1–2 (2–3 when the GPU is free) |
| RF-DETR Small | 512 px | ~430 ms | 95.7% | 1 at ~2 fps |

Keep **Nano** unless the pilot shows missed detections on hard scenes (people far away, dark, crowded). To try Small:

```powershell
cd services\vision
uv run --group export python ..\..\scripts\export_model.py small    # once
# then in .env:  MODEL_PATH=models/rfdetr-small.onnx   and restart the worker
```

To cut false alarms first, use a rule's **Minimum confidence** setting rather than a bigger model.

## Adding a second camera

- **Another phone:** stream Larix to `rtsp://<laptop IP>:8554/cam2`, then add a camera with `rtsp://127.0.0.1:8554/cam2` in the dashboard.
- **An IP camera (RTSP/ONVIF):** add it with its substream URL, for example `rtsp://user:pass@192.168.1.50:554/stream2`. Use **Test** on the camera card to check it.
- **Capacity:** about 2–3 cameras at 3 fps on this laptop's GPU with the Nano model. Use `uv run python -m app.benchmark` to check yours.

## Tests

```powershell
cd services\backend; uv run pytest      # 43 API tests: auth, roles, CSRF, rules, events, analytics, retention
cd services\vision;  uv run pytest      # 18 worker tests: rules engine, counts, ingest, events
```

**End-to-end test** (whole pipeline: stream, worker, API, live push). It needs the API running and `E2E_ADMIN_USERNAME` / `E2E_ADMIN_PASSWORD` in `.env`:

```powershell
cd services\vision
uv run python ..\..\scripts\make_e2e_clip.py --image <any snapshot or photo with a person>   # once
cd ..\backend
uv run python ..\..\scripts\e2e.py
```

It adds a temporary camera and rules, streams a person walking through a zone and across a tripwire, and checks:
- the alerts arrive with snapshots over the WebSocket
- review works and records the reviewer
- people counts reach analytics, and CSV export works
- operators can't change settings
- losing the stream raises an offline alert
- the audit log records everything

Then it removes the temporary camera and rules.

## Deploying to an edge box (Docker)

```powershell
docker compose --profile full up -d --build    # MediaMTX, PostgreSQL, API + dashboard, worker
```

- **Dashboard:** `http://<box>:8000`. For access from other machines, change the API port binding in `compose.yaml`.
- **Camera URLs:** cameras that go through MediaMTX are registered as `rtsp://mediamtx:8554/<path>`.
- **Intel iGPU on a Linux host:** uncomment the `/dev/dri` device for the `vision` service and set `CONTAINER_INFERENCE_DEVICE=GPU`.
- **Model:** the worker reads the ONNX model from `./models`.
- **Ports:** set `API_PORT` in `.env` if 8000 is already in use.
- **Memory:** give Docker at least **4 GB of RAM**. The worker image is about 2.9 GB (OpenVINO and OpenCV), so on a 2 GB Docker Desktop VM with little free RAM the worker container may never start. On a laptop, the native setup above is lighter.

*Tested:* the end-to-end test passes 18/18 against the Docker stack (API on port 8090, worker on OpenVINO CPU in its container). Command, run from `services\backend`:

```powershell
$env:E2E_API="http://127.0.0.1:8090"; $env:E2E_CAMERA_RTSP="mediamtx:8554"; uv run python ..\..\scripts\e2e.py
```

If Docker Desktop stops starting containers (it happens when the host runs out of memory), restart it: `docker desktop restart`.

Don't run the Docker `api`/`vision` containers alongside the native API and worker against the same database, or both workers will process every camera.

## Backups

```powershell
.\scripts\backup.ps1 [-IncludeSnapshots]                                  # -> backups\aivision-<time>.dump
.\scripts\restore.ps1 -Dump backups\<file>.dump -Database restore_check   # verify into a scratch database
.\scripts\restore.ps1 -Dump backups\<file>.dump                           # restore over live data (stop API + worker first)
```

## Security notes

- **Logins:** argon2 password hashes and server-side sessions in an HttpOnly, SameSite cookie. Every change needs a CSRF header, and logins lock for 15 minutes after 5 failures.
- **Protected data:** snapshots and live updates require a session. Camera passwords are encrypted and redacted from logs, errors and the audit log.
- **Network:** the API binds to 127.0.0.1 by default. Before exposing it, put it behind HTTPS (set `COOKIE_SECURE=true`) and change the MediaMTX publish password in `infra/mediamtx/mediamtx.yml`.
- **Retention:** events, snapshots and counts are deleted automatically after the retention period set in Settings (default 30 / 90 days).
- **Human review:** alerts describe what was detected, not intent. A person should review every alert.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Camera shows *Offline* | Is the phone streaming, or the camera reachable? Use **Test** on the camera card. Check that phone and laptop are on the same Wi-Fi |
| No zone alerts | Draw zones on the floor; make sure the person's feet (or body, if cut off) are inside; check the rule is enabled and detects `person` |
| Slow or low fps | Plug the laptop in (battery throttles the CPU); run `uv run python -m app.benchmark` in `services\vision` |
| `localhost` is slow or hangs | Use `127.0.0.1`: on Windows `localhost` tries IPv6 first, and Docker only listens on IPv4 |
| Forgot the admin password | Another admin can reset it under Settings → Users. If you are the only admin, restore a backup or reset the database |

## Other tools

- **Benchmark:** `uv run python -m app.benchmark` (in `services\vision`) shows how many cameras this machine can handle.
- **Standalone worker (no API):** leave `API_BASE_URL` empty. The worker then reads `CAMERA_SOURCES` and `config/rules.json` (draw with `uv run python -m app.draw_rules`) and writes `storage/events/`.
- **API docs:** http://127.0.0.1:8000/docs

## Project layout

```
apps/web/            dashboard (Vite + React)
services/backend/    API (FastAPI), migrations, tests
services/vision/     worker: ingest, detector, tracker, rules, counts, tests
infra/mediamtx/      stream server config
scripts/             run-api, test-stream, e2e, backups, model export
models/ samples/ storage/ backups/   local data (git-ignored)
```
