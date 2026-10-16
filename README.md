# AI Vision Edge

Local-first AI analytics for IP cameras. See [ai-vision-edge-project.md](ai-vision-edge-project.md) for the full spec.

**Current milestone:** vision worker (ingest → detect → track) tested with an Android phone and looping video files as cameras. Everything here is free and runs on this laptop.

```
Android phone (Larix) ──RTSP publish──┐
fake-cameras.ps1 (looping clips) ─────┤──> MediaMTX :8554 ──RTSP read──> vision worker (Python)
real IP camera (later, pulled) ───────┘          └──WebRTC :8889──> browser
```

| Piece | Choice | License |
|---|---|---|
| Detector | RF-DETR Nano, exported to ONNX | Apache-2.0 |
| Inference | OpenVINO on the Intel Iris Xe GPU; ONNX Runtime CPU fallback | Apache-2.0 / MIT |
| Ingest | PyAV (FFmpeg) | BSD |
| Tracking, zones, lines | supervision (ByteTrack, PolygonZone, LineZone) | MIT |
| Stream server | MediaMTX 1.21.2 | MIT |

## One-time setup

```powershell
copy .env.example .env
cd services\vision
uv sync
uv run --group export python ..\..\scripts\export_model.py nano   # writes models\rfdetr-nano.onnx
```

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

## Worker output

One JSON line per camera per second:

```json
{"camera": "cam1", "status": "online", "infer_fps": 3.0, "avg_infer_ms": 41.2, "frame_age_ms": 38, "objects": {"person": 2}, "track_ids": [4, 7]}
```

`frame_age_ms` is how stale the frame was when it was analyzed. It should stay low; if it climbs, the machine is overloaded.

## Next milestones

1. Zone/line/loitering rules + event snapshots (supervision `PolygonZone`/`LineZone`), tested against recorded clips.
2. FastAPI + PostgreSQL: camera registry, event API, WebSocket.
3. React dashboard: overview, cameras (zone drawing), events.
