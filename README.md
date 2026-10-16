# AI Vision Edge

Local-first AI analytics for IP cameras. See [ai-vision-edge-project.md](ai-vision-edge-project.md) for the full spec.

**Current milestone:** vision worker (ingest → detect → track → rules → events) plus the API (camera registry, events, live updates), tested with an Android phone and looping video files as cameras. Everything here is free and runs on this laptop.

```
Android phone (Larix) ──RTSP publish──┐
fake-cameras.ps1 (looping clips) ─────┤──> MediaMTX :8554 ──RTSP──> vision worker ──HTTP──> API :8000 ──> PostgreSQL
real IP camera (later, pulled) ───────┘          └──WebRTC :8889──> browser    (cameras, status, events)   └─WebSocket──> dashboard
```

| Piece | Choice | License |
|---|---|---|
| Detector | RF-DETR Nano, exported to ONNX | Apache-2.0 |
| Inference | OpenVINO on the Intel Iris Xe GPU; ONNX Runtime CPU fallback | Apache-2.0 / MIT |
| Ingest | PyAV (FFmpeg) | BSD |
| Tracking, zones, lines | supervision (ByteTrack, PolygonZone, LineZone) | MIT |
| Stream server | MediaMTX 1.21.2 | MIT |
| API | FastAPI, SQLAlchemy 2, Alembic | MIT |
| Database | PostgreSQL 17 | PostgreSQL License |
| Dashboard | Vite, React, TypeScript, Tailwind CSS, TanStack Query | MIT |

## One-time setup

```powershell
copy .env.example .env      # then generate CAMERA_SECRET_KEY / SERVICE_TOKEN (commands are in the file)
docker compose up -d        # MediaMTX + PostgreSQL
cd services\vision
uv sync
uv run --group export python ..\..\scripts\export_model.py nano   # writes models\rfdetr-nano.onnx
cd ..\backend
uv sync
uv run alembic upgrade head # create/upgrade database tables
```

## Daily run (2 terminals)

```powershell
docker compose up -d                                                   # if not already running
cd services\backend; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
cd services\vision;  uv run python -m app.main --show
```

Open the dashboard at **<http://127.0.0.1:8000>** (FastAPI serves the built dashboard from `apps/web/dist`).
API docs: <http://127.0.0.1:8000/docs>

### Dashboard development

```powershell
cd apps\web
npm install
npm run dev      # http://localhost:5173, hot reload; proxies /api and /media to the API on :8000
npm run build    # rebuild apps/web/dist so http://127.0.0.1:8000 serves the latest version
```

Pages: **Overview** (camera health, events in the last 24 h, recent events), **Events** (filter, open a snapshot, acknowledge / resolve / mark as false alarm with a note), **Cameras** (add, edit, test connection, enable/disable, delete, and live video for streams that go through MediaMTX). Everything updates live over a WebSocket; new events also pop up as notifications.

Add a camera (the worker picks it up within 10 s):

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/cameras -ContentType application/json `
  -Body '{"name": "Phone (Larix)", "stream_url": "rtsp://127.0.0.1:8554/cam1"}'
```

Rules in `config/rules.json` are keyed by `cam<camera id>` (camera id 1 = `cam1`).

## API overview

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/cameras`, `GET/PATCH/DELETE /api/cameras/{id}` | Camera registry. Stream URLs are encrypted at rest; passwords are masked (`***`) in every response |
| `POST /api/cameras/{id}/test` | Try to connect and decode one frame |
| `GET /api/events?camera_id=&event_type=&status=&severity=&since=&until=&limit=&offset=` | Event list, newest first |
| `GET/PATCH /api/events/{id}` | Event detail; review with `{"status": "acknowledged" \| "resolved" \| "false_alarm", "note": ..., "reviewed_by": ...}` |
| `GET /api/analytics/summary?hours=24` | Camera status counts, events by type, unreviewed count |
| `GET /media/events/<path>` | Event snapshots |
| `WS /api/ws` | Live push: `event`, `event_updated`, `camera`, `camera_status`, `camera_deleted` |
| `/api/internal/*` | Worker only (`X-Service-Token` header): camera list with real URLs, status heartbeats, event ingest |

The worker also runs without the API: leave `API_BASE_URL` empty and it uses `CAMERA_SOURCES` and only writes `storage/events/`.

## 1. Benchmark this laptop

```powershell
cd services\vision
uv run python -m app.benchmark
```

The `cameras` column shows how many streams each device can run at `INFERENCE_FPS`. If you have a recorded clip, pass `--video ..\..\samples\clip.mp4` to benchmark on real footage.

- **Plug the laptop in and close heavy apps first.** On battery, Windows throttles the CPU and the numbers get much worse.
- The first GPU run takes about a minute to compile the model. After that it's cached in `models\.ov_cache` and startup takes seconds.
- First measurement (i7-1185G7 on battery, CPU already busy): GPU about 110 ms/frame, so about 2 cameras at 3 fps. Expect more when plugged in.

## 2. Use the Android phone as a camera

1. Start MediaMTX (from the repo root): `docker compose up -d mediamtx`
2. Find the laptop's Wi-Fi IP address: `ipconfig` (the IPv4 address under "Wireless LAN adapter Wi-Fi", e.g. `192.168.1.23`).
3. On the phone, install **Larix Broadcaster** (free on Google Play).
4. In Larix: **Settings → Connections → New connection**
   - URL: `rtsp://192.168.1.23:8554/cam1`
   - Mode: Video only
   - Login: `publisher` / Password: `changeme-publish`
5. In Larix: **Settings → Video**: resolution **640×480** (or 1280×720) and **15 fps**. This mimics a CCTV substream.
6. Tap the record button to start streaming.
7. Check it on the laptop: open <http://localhost:8889/cam1> in a browser.
8. Run the worker:
   ```powershell
   cd services\vision
   uv run python -m app.main --show
   ```

Phone and laptop must be on the same Wi-Fi. If the phone can't connect, Windows Firewall is probably blocking port 8554. Allow Docker Desktop on **Private** networks when Windows asks, or add an inbound rule for TCP 8554. Guest and public Wi-Fi networks often block device-to-device traffic.

Tips: keep the phone plugged in, mount it high and angled down like a real CCTV camera, and only film places you're allowed to.

## 3. Simulate more cameras with video files

Put `.mp4` clips in `samples\` (your own phone recordings, or free stock from Pexels/Pixabay; search for "cctv" or "parking lot"). Then:

```powershell
.\scripts\fake-cameras.ps1          # clips become cam2, cam3, cam4
```

Set `CAMERA_SOURCES` in `.env` to all four `rtsp://localhost:8554/camN` URLs and run the worker. You can also skip MediaMTX and run on a file directly:

```powershell
uv run python -m app.main --source ..\..\samples\clip.mp4 --show
```

## 4. Zones, tripwires and events

Draw rules on the live phone view (the worker can keep running; it reloads rules within ~2 s):

```powershell
cd services\vision
uv run python -m app.draw_rules            # cam1; use --camera cam2 for others
```

| Key | Rule | Fires when |
|---|---|---|
| `1` | Zone entry | a person's feet enter the zone |
| `2` | Tripwire | a person crosses the line (2 clicks) |
| `3` | Loitering | a person stays in the zone longer than `dwell_seconds` (default 20) |
| `4` | Occupancy | more than `max_count` people are in the zone (default 3) |

Click points, press **Enter** to finish a zone, **u** to undo a point, **Esc** to cancel, **d** to delete the last rule, **q** to quit.
Rules are saved to `config/rules.json`. Edit it to rename rules or change `classes`, `severity`, `cooldown_seconds`, `dwell_seconds`, `max_count`, or a tripwire's `direction` (`in`/`out`/`both`). See [config/rules.example.json](config/rules.example.json).

Draw zones **on the floor**: objects are located by the bottom-center of their box (where feet touch the ground).

Events are written to `storage/events/`:

- `events.jsonl`: one JSON event per line
- `YYYY/MM/DD/<event>.jpg`: snapshot with the zone and the person highlighted

The worker also raises `camera_offline` after `OFFLINE_ALERT_SECONDS` (default 15) without frames, and `camera_online` when the camera recovers.

## Worker output

One JSON line per camera per second:

```json
{"camera": "cam1", "status": "online", "infer_fps": 3.0, "avg_infer_ms": 41.2, "frame_age_ms": 38, "objects": {"person": 2}, "track_ids": [4, 7]}
```

`frame_age_ms` is how stale the frame was when it was analyzed. It should stay low; if it climbs, the machine is overloaded.

## Next milestones

1. Operator login (the API and dashboard are currently open to anyone who can reach 127.0.0.1).
2. Rules stored in the database and drawn in the dashboard instead of `draw_rules`.
3. Retention (auto-delete old snapshots/events), analytics trends, CSV export.
