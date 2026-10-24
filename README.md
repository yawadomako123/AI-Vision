# AI Vision Edge

Local-first AI video analytics for IP cameras: detect and track people and vehicles, raise alerts from zones and lines, count entries and exits, and review everything in a live web dashboard. Everything runs on one machine, and every component is free and open source. See [ai-vision-edge-project.md](ai-vision-edge-project.md) for the original spec.

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
| **Live** | All cameras at once in a 1, 2×2 or 3×3 wall with detection boxes and track numbers, a red flash and label when a camera raises an alert, full screen per camera or for the whole wall. MediaMTX cameras play as smooth WebRTC video; others show a preview refreshed every second. Boxes and previews are only sent while the page is open |
| **Events** | Filter, open a snapshot, then **Acknowledge**, **Resolve** or mark **False alarm** with a note (your username is recorded) |
| **Cameras** | Add, edit, test, enable or disable, and delete cameras; **Live view**; **Rules** opens the rule editor |
| **Rules** (per camera) | Draw **Zones**, **Lines** and **Loitering** areas (**Occupancy** is under *More rule types*) on a current camera image, drag corners to adjust, and set severity, classes, timing and direction. The worker applies changes within 3 s |
| **Analytics** | People and vehicles in view per hour or day, entries and exits from lines, events by type, rule false-alarm rates, CSV export (24 h / 7 d / 30 d, per camera) |
| **Settings** | System health, data retention, your password, users and roles (admins), audit log (admins) |

**Arm / Disarm:** the switch at the top of the sidebar. While disarmed, rules don't raise alerts (for example while you're home). Camera-offline alerts keep working, and every arm and disarm is recorded in the audit log. Operators can arm and disarm too. Lines keep counting entries and exits while disarmed.

**Lines (entry/exit counting and tripwires):** draw a **Line** across a door, gate or driveway. An arrow shows which way counts as *in*. Every line counts people and vehicles crossing each way. Tick **Alert on crossing** to also raise an alert, optionally for one direction only (a tripwire); leave it off for a count-only line. Totals per line and per hour/day are in **Analytics → Entries and exits**, with CSV export. Only minutes with crossings are stored, so it costs almost nothing.

**Active hours:** in the rule editor, set a rule to *Only at certain times*, for example weekdays 22:00–06:00. Windows that cross midnight run into the next morning. Times are the edge computer's local time.

**Clips:** each rule alert saves a short clip (about 3 s before to 5 s after, 6 fps, ≤640 px, usually 30–300 KB), shown in the event dialog with a Clip / Snapshot toggle.
- Alerts that fire close together share one clip, and nothing is recorded continuously.
- Clips have their own retention (default 7 days) and a total storage cap (default 1 GB, oldest deleted first).
- Settings → Data retention can turn clips off.

**Counting accuracy:** extra boxes on the same person (for example one for the whole body and one for the head) are removed automatically. **Settings → Performance → Detection sensitivity** sets how sure the model must be: *High* catches more in poor light but adds false or doubled boxes, *Balanced* is the default, and *Low* only counts very clear people and vehicles. It applies live.

**Recording and Playback:** every camera is recorded continuously by MediaMTX, exactly as received (no re-encoding, almost no CPU), into `storage\recordings`. **Playback** shows a 24-hour bar per camera and day: shaded where something was recorded, with alerts marked in colour. Click anywhere to watch from that moment, or use **Watch recording** on any event or search result. **Settings → Recording** sets how many days to keep (default 7) and a storage limit (default 20 GB, oldest deleted first); at least 5 GB of disk is always kept free. Expect roughly 5–10 GB per camera per day at 640×480.

IP cameras are routed through MediaMTX automatically, so the camera serves one connection that the worker, the live video and the recorder share; every camera now has smooth live video in Live.

**Privacy masks:** in a camera's rules, *More rule types → Privacy mask* blacks out an area (a neighbour's window, a public street). It's blanked the moment each frame arrives, so it's never analysed and never appears in snapshots, alert clips, search images or the Live page; masks stay on while disarmed. The camera's own stream isn't changed, so check what the camera itself records if it has an SD card.

**Tamper alerts:** each camera learns its normal view and raises a high-severity *Camera tampering* alert (with a snapshot) when it's covered or blacked out, smeared or out of focus, or moved/turned away for 10 s, and a *back to normal* event afterwards. A camera deliberately re-aimed is accepted as the new normal after 5 minutes. Switching the lights off in a room looks the same as covering the lens, so it's reported as "too dark". On by default (Settings → Performance).

**Motion skip:** on cameras where nothing moves and nobody is being tracked, the AI rests after 3 s and only checks one frame every 2 s; any movement wakes it straight away. On by default (Settings → Performance).

**Performance:** Settings → Performance sets the *analysis speed* (frames checked per second per camera: 1, 2, 3 or 5). Use 1–2 on a weak PC or with many cameras; 3 is the default. It applies live.

**Search (plain English):** the **Search** page finds anything the cameras saw, not just alerts. Type something like *"person in a white hoodie"* or *"white car"*.
- **What's saved:** every tracked person or vehicle becomes a *sighting*: a small cut-out, a camera view with the path they walked, first and last seen times, and a SigLIP "meaning fingerprint". That's about 25 KB per sighting, with no video.
- **Results** are ranked by relevance (strong / possible / weak). Opening one shows the path and any alert clip from that time.
- **Vehicles** are labelled with their **colour** and **body type** (saloon, hatchback, SUV, pickup, van, minibus, truck, bus, motorcycle, bicycle), and the Search page can filter by both (choose *Vehicles*, then a colour/type). It reuses the search model: no extra AI, almost no extra work. When lights flood the picture at dusk or night (brake lights, headlight glare) it says nothing rather than guess: on 90 real test crops, daylight colours were mostly right, and at dusk 38 of 45 cars were correctly marked unknown instead of being called "red". Known weak spot: silver/grey cars can come out "blue" in footage with a blue colour cast.
- **Number plates** (off by default; Settings → Number plates, needs Search on): each vehicle's plate is read from its 3 largest pictures and the reads are combined character by character, since single reads often miss one. Results show the plate **text and its picture**: the picture is the evidence. Search by plate under *Vehicles* (partial plates and one wrong character still match, and O/0, B/8, I/1 count as the same). The **watchlist** raises a high-severity *Watched plate seen* alert, pushed to your phone, or just puts a name to a plate. Uses the free fast-alpr models (MIT, downloaded once, about 10 MB), about 140 ms per vehicle on the CPU. Accuracy depends mostly on the camera: aim one at a gate or entrance, so the plate is at least ~130 px wide, with a fast shutter and IR at night. Plates are personal data: put up a camera notice; they're deleted with sightings. Signs and on-screen text can be mistaken for plates: anything found in the same spot and looking the same on 3+ vehicles is ignored, rows of lights are rejected, and you can cover a sign with a **privacy mask** so it's never read. In testing on traffic footage, false readings went from 12 to 0–3 per 100 vehicles with these rules; real-plate accuracy still needs testing on a camera aimed at a gate.
- **Find similar** lists other sightings that *look* alike, on any camera, in time order. It's a lead based on appearance, not an identification.
- **Retention:** sightings are kept 14 days (Settings).
- **Off by default:** it uses about 0.5–0.7 GB of extra memory, so turn it on in **Settings → Performance** once the model is installed. It applies within seconds, no restart. Turning it off frees the memory.
- **Setup:** search needs the model once:

  ```powershell
  cd services\vision
  uv run --group export python ..\..\scripts\export_siglip.py
  ```

  It writes `models\siglip`, about 470 MB: Google's SigLIP, Apache-2.0.

**Roles:** *admins* manage cameras, rules, users and settings. *Operators* watch and review events. New alerts pop up live, and everything updates without reloading.

**Drawing tips:** people are located by their feet (the bottom of their box), so draw zones on the floor. If a person is cut off by the bottom of the image, the middle of the box is used instead.

## First-time setup

**The easy way (Windows):** install the free tools (Git, uv, Node.js, FFmpeg; the script prints the exact `winget` commands for anything missing). **No Docker needed:** the database is a SQLite file and the camera hub (MediaMTX) is a native program the script downloads and checksum-verifies. Then:

```powershell
git clone https://github.com/yawadomako123/AI-Vision.git
cd AI-Vision
.\scripts\setup.ps1
```

It generates all secrets into `.env`, sets up SQLite and the native MediaMTX, installs everything, downloads the detection model, prepares the database, and can turn on start-at-login and a cloud-folder copy of the nightly backups. It's safe to run again: it never replaces secrets you already have. Then open http://127.0.0.1:8000 and create your admin account. Add `-WithSearch` for the plain-English search model.

**By hand** (what the script does):

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
- User: `publisher`, password: the `MEDIAMTX_PUBLISH_PASSWORD` value in `.env` (generated at setup; Docker passes it to MediaMTX)
- Video: 640×480, 15 fps

In the dashboard, add a camera with stream URL `rtsp://127.0.0.1:8554/cam1`. Mount the phone high and angled down, keep it plugged in, and only film places you're allowed to. If the phone can't connect, allow Docker Desktop on *Private* networks in Windows Firewall (TCP 8554).

**Real IP camera: use Find cameras.** Connect the camera to the same router or Wi-Fi as this computer, then go to **Cameras → Find cameras**. It searches the local network in about 5 seconds, two ways:
- **ONVIF discovery:** most IP cameras (Hikvision, Dahua, Reolink, Tapo, Uniview, Axis, Amcrest…) answer with their name and model. Enter the camera's username and password and press **Get streams**. The camera itself returns its stream addresses, and the lowest-resolution one is marked *recommended* (lightest to analyse).
- **RTSP port sweep:** this also finds cameras with ONVIF switched off. When the brand is recognised, it offers ready-made substream and main-stream addresses.

Pick one and the **Add camera** form opens pre-filled. Press **Check stream** to confirm it works before saving. Only private (local) addresses are ever contacted. When the API runs inside Docker (`--profile full`), it can't see your home network, so Find cameras only works with the API running natively (`scripts/run-api.ps1`) or with host networking on Linux. If a camera isn't found, switch on ONVIF/RTSP in the camera's own app (Tapo and Reolink have it off by default and need a separate "camera account").

**Adding by hand:** enter the RTSP URL (prefer the low-resolution substream) (prefer the low-resolution substream), for example `rtsp://user:pass@192.168.1.50:554/stream2`. Passwords are encrypted in the database and never shown again. For live view in the browser, also add the camera as a MediaMTX source (see `infra/mediamtx/mediamtx.yml`) and register `rtsp://127.0.0.1:8554/<path>` instead.

**Public live webcams (testing without a camera):** `scripts/live-test.py` runs the whole system on any live stream for a few minutes. It adds a temporary camera with a zone and a count-only line, prints status every 30 s, then removes it. It accepts RTSP, HLS (`.m3u8`) or a YouTube live link (resolved with `yt-dlp`; those addresses expire after a few hours, so they suit tests only):

```powershell
cd services\backend
uv run python ..\..\scripts\live-test.py "https://www.youtube.com/watch?v=<live id>" --minutes 5
uv run python ..\..\scripts\live-test.py rtsp://user:pass@192.168.1.64/Streaming/Channels/102   # a real camera
```

Pick street-level cameras: in wide aerial shots people are only a few pixels tall and too small to detect (vehicles still work). HLS web feeds run about 30 s behind real time, which is how HLS works; RTSP cameras are near real time. Only use public streams the owner publishes for viewing, never unsecured private cameras found online.

**Test clips without a camera:**

```powershell
.\scripts\test-stream.ps1                                  # color bars on cam1
.\scripts\test-stream.ps1 -Path cam2 -Clip samples\clip.mp4 # loop a recording as cam2
```

## Running unattended (auto-restart)

`scripts/supervise.ps1` runs everything without terminal windows and keeps it running:

```powershell
.\scripts\supervise.ps1              # start Docker services, the API and the worker; Ctrl+C stops all
.\scripts\install-autostart.ps1      # start it automatically when you log in (no admin needed)
.\scripts\install-autostart.ps1 -Remove
```

- **Crash recovery:** a crashed API or worker restarts within about 10 s. Repeated crashes back off up to 5 min.
- **Hang detection:** restarts the API if `/health` stops answering, and the worker if its heartbeat file (`storage/worker.heartbeat`, refreshed every 5 s) goes stale for 2 min.
- **Docker:** starts Docker Desktop if it isn't running (no need for its *Start when you sign in* setting), waits for it, then starts the database and MediaMTX.
- **Nightly backup:** after 02:00 each day it backs up the database to `backups\` and keeps the newest 14 (see *Backups*).
- **Logs:** `storage/logs/` (`supervisor.log`, `api*.log`, `worker*.log`; the previous run is kept as `*.prev.log`).
- **Phone alerts when the system itself breaks:** with phone notifications on, you get a push when the vision worker stops reporting for 2 min or the disk drops below 2 GB / 5 % free, and another when it's fixed. Current problems also show at the top of **Settings → System health**.

**Endurance test:** `scripts/soak-test.py` streams N looping fake cameras for as long as you like and writes a report to `storage/soak-*.md`. It records online %, frames analysed per camera, worker memory and CPU, restarts, and alert delay:

```powershell
cd services\backend
uv run python ..\..\scripts\soak-test.py --cameras 4 --minutes 240
```

Measured on this laptop (i7-1185G7, Iris Xe, search on, 6 min each):

| Cameras | Online | Frames analysed per camera | Worker RAM | Worker CPU | Alert delay (median / 95%) |
|---|---|---|---|---|---|
| 2 | 100 % | 2.9 /s | ~700 MB | ~6 % | – |
| 4 | 100 % | 2.3 /s | ~770 MB (flat) | ~11 % | 0.18 s / 0.23 s |

The integrated GPU manages about 9–12 detections a second in total, shared between cameras (image preparation runs on the GPU as part of the model). These figures are with constant movement; with motion skip, quiet cameras use almost nothing, leaving the GPU to the busy ones.

## Windows installer (for customers)

One `.exe` that sets up everything with no Docker, Git, Node.js or Python needed on the customer's PC:

```powershell
winget install --id JRSoftware.InnoSetup -e   # once, on the build PC (free)
.\scriptsuild-installer.ps1                 # -> dist\AIVisionEdge-Setup-<date>.exe
```

The build bundles the app from git `HEAD` (commit first), the pre-built dashboard, the detection model, MediaMTX, [uv](https://github.com/astral-sh/uv) and [WinSW](https://github.com/winsw/winsw); every download is checksum-verified. The installer (needs administrator):

- installs to `C:\AIVisionEdge` (a short path, because Python libraries nest deeply);
- downloads Python and the AI libraries into that folder (about 1.5 GB, several minutes), creates the secrets and the SQLite database;
- registers the **AI Vision Edge** Windows service, which starts at boot (even before anyone logs in) and is restarted by Windows if it stops. The service runs `supervise.ps1`;
- allows cameras on **private** networks to reach the camera hub (TCP 8554). Everything else listens only on this PC.

Re-running a newer installer upgrades in place and keeps `.env`, data and recordings. Uninstalling removes the service and the firewall rule and keeps your data. Setup log: `storage\logs\install.log`; service logs are in `storage\logs\`. Without the installer, `.\scripts\install-service.ps1` (as administrator) turns any setup.ps1 install into a service.

## Phone notifications (ntfy, free)

1. Install the free **ntfy** app on your phone.
2. In the dashboard, go to **Settings → Phone notifications**. Click **Generate** for a private topic, tick **Send alerts to my phone**, and click **Save**.
3. In the ntfy app, tap **+** and subscribe to that exact topic (server `ntfy.sh`). Back in the dashboard, click **Send test**.

You'll then get high-severity alerts on your phone, at most one per rule per minute (both are adjustable).

**Buttons on alerts:** when a **Dashboard link** is set (your Tailscale address), each alert notification has **Acknowledge** and **Mute camera 1 h** buttons. The phone sends them straight to your dashboard over Tailscale. Each button only works for that one alert or camera and expires after 2 hours. *Mute* silences phone notifications from that camera only: detection, recording and the dashboard carry on. Cameras also have **Mute 1 h** / **Unmute** in the dashboard, and every use is in the audit log.

**Daily summary:** every morning at 08:00 (adjustable, or off) you also get one message about the last 24 hours, for example *"Last 24 h: 12 alerts (9 zone entry, 3 loitering), 3 to review. 214 in / 209 out. At most 6 people at once. All 3 cameras online."* **Send summary now** shows what it looks like.

**System alerts:** you're also told when the vision worker stops or the disk is nearly full, whatever the severity setting (see *Running unattended*).

**Privacy:** on the public `ntfy.sh` server, anyone who knows the topic can read the alerts, so keep the generated topic secret. Snapshot images are **not** attached by default. To keep everything, images included, on your own network, self-host ntfy:
- Run `docker compose --profile ntfy up -d`.
- Use server `http://<laptop IP>:2586` in Settings and in the app.
- The phone needs to reach that address: the same Wi-Fi, or Tailscale when you're away.

## Remote access (Tailscale, free)

To open the dashboard on your phone from anywhere, without opening ports to the internet:

1. Install **Tailscale** on the laptop and the phone, and sign in with the same account on both.
2. On the laptop, run `tailscale serve --bg 8000`. It prints an HTTPS address like `https://my-laptop.tail1234.ts.net`.
3. Open that address on your phone. Optionally, paste it as the **Dashboard link** in Settings → Phone notifications, so tapping an alert opens it.

4. **Install it as an app:** in Chrome on Android, open the menu and choose **Add to Home screen** (or **Install app**). On iPhone, use Safari: **Share → Add to Home Screen**. It then opens full screen like a normal app, with a bottom tab bar (Overview, Live, Events, Search, More). Installing needs the HTTPS address from step 2.

Only devices signed in to your Tailscale account can reach it. The API notices HTTPS and marks the login cookie `Secure` automatically. Over Tailscale the **Live** wall shows each camera as a picture refreshed every second, with detection boxes. Smooth video needs the local network, because the video stream server isn't exposed.

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

## Storage

**Installed software** (measured on the dev laptop):

| Part | Size |
|---|---|
| Vision worker (Python + OpenVINO + PyAV) | 1.3 GB |
| API (Python) | 0.25 GB |
| Detection model (RF-DETR Nano) | 0.11 GB |
| Search model (SigLIP, optional) | 0.65 GB |
| PostgreSQL + MediaMTX + ntfy images (Docker) | 0.6 GB |
| Dashboard build tools (development only) | 0.1 GB |
| **Total** | **about 3 GB** (2.4 GB without search) |

The all-in-Docker deployment (`--profile full`) uses about 4.5 GB of images instead.

**Data it keeps.** There's no 24/7 recording, so data grows slowly and every type is capped by a retention setting:

| What | Size each (measured) | Kept (default) |
|---|---|---|
| Alert: snapshot + database row | ~40 KB | 30 days |
| Alert clip (~8 s, low frame rate) | ~40 KB | 7 days, 1 GB cap |
| Sighting (only with search on) | ~25 KB | 14 days |
| People/vehicle and entry/exit counts | ~0.3 MB per camera per day | 90 days |

Example: a home camera with 50 alerts and 300 people/vehicles passing a day uses about 4 MB a day of alerts and 8 MB a day of sightings. At the default retention that levels off around **250 MB per camera**, or about 1 GB for four cameras. A busy shopfront with hundreds of alerts a day might reach 1–2 GB per camera. **Settings → System health** shows actual usage and free disk space.

For comparison, continuous 24/7 recording (not part of the MVP) would need roughly 5–15 GB per camera per day even at substream quality.

## Tests

```powershell
cd services\backend; uv run pytest      # 79 API tests: auth, roles, CSRF, rules, events, analytics, retention, discovery
cd services\vision;  uv run pytest      # 32 worker tests: rules engine, counts, ingest, events
```

**End-to-end test** (whole pipeline: stream, worker, API, live push). It needs the API running and `E2E_ADMIN_USERNAME` / `E2E_ADMIN_PASSWORD` in `.env`:

```powershell
cd services\vision
uv run python ..\..\scripts\make_e2e_clip.py --image <any snapshot or photo with a person>   # once
cd ..\backend
uv run python ..\..\scripts\e2e.py
```

It adds a temporary camera and rules, streams a person walking through a zone and across a line, and checks:
- the alerts arrive with snapshots over the WebSocket
- review works and records the reviewer
- people counts and counter-line entries/exits reach analytics, and CSV export works
- operators can't change settings
- losing the stream raises an offline alert
- the audit log records everything

Then it removes the temporary camera and rules.

## Deploying to an edge box

**Recommended on Windows: run natively with the supervisor.** It's lighter than Docker (no 2.9 GB worker image, no VM memory limit), it uses the Intel GPU directly, and **Find cameras** can see your network. Set it up once:
1. Follow *First-time setup*.
2. Run `.\scripts\install-autostart.ps1`.
3. Turn on Docker Desktop's *Start when you sign in* (Docker only runs the database and MediaMTX).

See *Running unattended*.

**Linux mini PC: everything in Docker.** Use this for a dedicated Linux box. Give the `api` service host networking (`network_mode: host`) if you want **Find cameras**, because from Docker's default network it can't see your home network.

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

## Database: SQLite or PostgreSQL

New installs keep everything in **one SQLite file** (`storage\aivision.db`), with no database server. Tested with 4 cameras writing at once: same speed and alert delay as PostgreSQL, no "database is locked" errors. PostgreSQL in Docker is still supported (leave `DATABASE_URL` empty), e.g. for very large sites.

**Moving an existing PostgreSQL install to SQLite:** stop the supervisor, then from `services\backend`:

```powershell
uv run python ..\..\scripts\migrate-to-sqlite.py --switch
```

It copies every table, checks the row counts and SQLite's integrity, sets `DATABASE_URL` in `.env` (the old line stays as a comment), and leaves PostgreSQL untouched, so switching back is one line.

## Backups

The supervisor backs up every night (keeps 14). By hand:

```powershell
.\scripts\backup.ps1 [-IncludeSnapshots]                                       # SQLite -> backups\aivision-<time>.sqlite, PostgreSQL -> .dump
.\scripts\restore.ps1 -Dump backups\<file> -Database storage\restore-test.db   # SQLite: check a backup into a separate file
.\scripts\restore.ps1 -Dump backups\<file>                                   # restore over live data (stop the supervisor first)
```

With PostgreSQL, `-Database restore_check` restores into a scratch database instead. SQLite restores are integrity-checked before they replace anything.

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
- **Standalone worker (no API):** leave `API_BASE_URL` empty. The worker then reads `CAMERA_SOURCES` and only detects and tracks (no rules): handy for benchmarks and quick tests on a video file. Rules, search and analysis speed are set in the dashboard.
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
