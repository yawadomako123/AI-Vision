# AI Vision Edge — CCTV Analytics MVP

**Project type:** Local-first AI video analytics for existing CCTV/IP cameras  
**Initial target:** Up to four cameras  
**Deployment:** Customer-premises edge computer, using existing hardware where possible  
**Status:** MVP specification and implementation plan

## 1. Product overview

AI Vision Edge is a local-first video analytics platform inspired by the capabilities of commercial CCTV intelligence products. It connects to authorized IP cameras, analyzes video locally, and gives operators a web dashboard for live monitoring, security events, and basic people/vehicle analytics.

The MVP focuses on measurable detection and configurable rules—not claims that AI can reliably infer intent or determine that a person is dangerous.

### MVP capabilities

- Connect to IP cameras through RTSP.
- Detect people and common vehicle classes.
- Track objects between frames within each camera view.
- Configure restricted zones and virtual tripwires.
- Generate events for zone entry, line crossing, loitering, occupancy thresholds, and camera outages.
- Save event timestamps, metadata, and evidence snapshots.
- Review, acknowledge, and resolve events.
- View basic people/vehicle counts and trends.
- Recover from camera and network interruptions.
- Run locally without requiring cloud video processing.

### Deferred features

- Facial identification and cross-camera identity matching.
- License-plate recognition.
- General-purpose fight/fall/weapon recognition.
- Natural-language search across recorded footage.
- Predictive threat scoring or automated punitive actions.
- Large-scale distributed deployment and billing.

## 2. Architecture

```text
Existing IP cameras
        |
        | RTSP (prefer camera substreams for inference)
        v
Local Vision Agent
  - Stream connection and reconnect
  - Frame sampling and bounded queues
  - Object detection
  - Multi-object tracking
  - Zone and line rules
  - Event generation
        |
        | Authenticated local API
        v
FastAPI Backend <----> PostgreSQL
  - Camera registry       - Cameras
  - Rule configuration    - Rules
  - Event APIs             - Events
  - Analytics              - Evidence metadata
  - WebSocket updates      - Audit records (later)
        |
        +----> Event snapshot storage
        |
        v
Next.js Web Dashboard
  - Overview
  - Live monitoring
  - Camera management
  - Events and alerts
  - Analytics
  - Settings

Optional MediaMTX service:
RTSP camera streams -> browser-compatible WebRTC/HLS playback
```

### Deployment principles

- Run inference locally to reduce recurring cloud costs, latency, and exposure of video.
- Start with one stream, benchmark, then scale to four.
- Use low-resolution camera substreams and sample around 3 inference frames/second/camera as an initial benchmark setting—not a guaranteed performance level.
- Keep the vision worker separate from the API process so video-processing failures do not necessarily take down the dashboard.
- Use bounded queues and drop stale frames rather than building up minutes of latency.
- Do not expose camera RTSP ports to the public internet.
- Add secure remote access only after the local pilot works.

## 3. Recommended technology stack

| Layer | Technology | Responsibility |
|---|---|---|
| Web frontend | Next.js, TypeScript, Tailwind CSS | Dashboard and operator workflow |
| API | Python, FastAPI, Uvicorn | Configuration, events, analytics |
| Database | PostgreSQL | Persistent structured data |
| ORM/migrations | SQLAlchemy 2.x, Alembic | Database models and schema changes |
| Video decoding | FFmpeg or GStreamer | RTSP ingest and decoding |
| Computer vision | YOLO-family detector or a commercially suitable alternative | Person/vehicle detection |
| Tracking | ByteTrack or BoT-SORT | Temporary per-camera object IDs |
| Live updates | WebSockets | Events and camera status |
| Browser video | MediaMTX with WebRTC/HLS | Optional camera playback in browser |
| Packaging | Docker Compose | Repeatable local deployment |
| Tests | Pytest | Backend and rule tests |

**Licensing gate:** Before commercial distribution, review the current license for the selected detector package and model weights. Some model ecosystems offer different open-source and commercial licensing terms. If the license does not fit the intended product, select a suitable alternative before committing to it.

## 4. Repository structure

```text
ai-vision-edge/
├── apps/
│   └── web/
│       ├── app/
│       │   ├── dashboard/
│       │   ├── cameras/
│       │   ├── events/
│       │   ├── analytics/
│       │   ├── settings/
│       │   ├── layout.tsx
│       │   └── page.tsx
│       ├── components/
│       │   ├── camera-grid.tsx
│       │   ├── camera-card.tsx
│       │   ├── event-table.tsx
│       │   └── status-badge.tsx
│       ├── lib/api.ts
│       ├── package.json
│       └── Dockerfile
├── services/
│   ├── backend/
│   │   ├── app/
│   │   │   ├── api/routes/
│   │   │   │   ├── cameras.py
│   │   │   │   ├── events.py
│   │   │   │   ├── rules.py
│   │   │   │   └── analytics.py
│   │   │   ├── core/config.py
│   │   │   ├── db/
│   │   │   │   ├── session.py
│   │   │   │   └── models.py
│   │   │   ├── schemas/
│   │   │   ├── services/
│   │   │   │   ├── event_service.py
│   │   │   │   └── camera_service.py
│   │   │   └── main.py
│   │   ├── migrations/
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── vision/
│       ├── app/
│       │   ├── main.py
│       │   ├── camera_manager.py
│       │   ├── frame_reader.py
│       │   ├── detector.py
│       │   ├── tracker.py
│       │   ├── rule_engine.py
│       │   ├── event_publisher.py
│       │   └── health.py
│       ├── requirements.txt
│       └── Dockerfile
├── infra/
│   └── mediamtx/
│       └── mediamtx.yml
├── storage/
│   └── .gitkeep
├── tests/
│   ├── test_rules.py
│   ├── test_events.py
│   └── test_camera_config.py
├── .env.example
├── .gitignore
├── compose.yaml
└── README.md
```

Build incrementally. The initial prototype only needs the vision worker's main loop, frame reader, and detector; add other modules as their responsibilities become necessary.

## 5. Version baseline

The following is a suggested reproducible starting baseline, not a claim that these are the newest releases. Verify compatibility, security updates, and model licensing before deployment.

| Component | Suggested baseline |
|---|---|
| Python | 3.11 |
| FastAPI | 0.115.12 |
| Uvicorn | 0.34.0 |
| SQLAlchemy | 2.0.40 |
| Alembic | 1.15.2 |
| psycopg | 3.2.6 |
| Pydantic Settings | 2.8.1 |
| Ultralytics | 8.4.120 (license review required) |
| OpenCV headless | 4.11.0.86 |
| Pytest | 8.3.5 |
| Node.js | 22 LTS |
| Next.js | 15.x, exact versions locked in package-lock |
| PostgreSQL | 16 |
| MediaMTX | Pin an exact tested release |

### `services/backend/requirements.txt`

```text
fastapi==0.115.12
uvicorn[standard]==0.34.0
sqlalchemy==2.0.40
alembic==1.15.2
psycopg[binary]==3.2.6
pydantic-settings==2.8.1
httpx==0.28.1
```

### `services/vision/requirements.txt`

```text
ultralytics==8.4.120
opencv-python-headless==4.11.0.86
httpx==0.28.1
pydantic-settings==2.8.1
```

Resolve and test the dependency graph, then pin all transitive dependencies for reproducible production builds.

## 6. Environment variables

Create `.env.example`:

```dotenv
POSTGRES_DB=aivision
POSTGRES_USER=aivision
POSTGRES_PASSWORD=replace_with_a_long_random_password

DATABASE_URL=postgresql+psycopg://aivision:replace_with_a_long_random_password@db:5432/aivision

API_BASE_URL=http://api:8000
NEXT_PUBLIC_API_URL=http://localhost:8000

MODEL_PATH=/models/yolo26n.pt
INFERENCE_SIZE=640
INFERENCE_FPS=3
DETECTION_CONFIDENCE=0.40

EVENT_STORAGE_PATH=/data/events
CAMERA_CONFIG_POLL_SECONDS=5
```

Copy this file to `.env`, replace the password, and keep `.env` out of version control. The sample model filename must match the model actually selected and legally approved for the product.

For production, configure browser access through a secured same-origin reverse proxy where practical. Do not expose internal services or camera credentials unnecessarily.

## 7. Docker Compose baseline

Create `compose.yaml`:

```yaml
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks: [internal]

  api:
    build:
      context: ./services/backend
    restart: unless-stopped
    env_file: .env
    environment:
      DATABASE_URL: ${DATABASE_URL}
      EVENT_STORAGE_PATH: /data/events
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - event_data:/data/events
    depends_on:
      db:
        condition: service_healthy
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
      interval: 15s
      timeout: 5s
      retries: 5
    networks: [internal]

  vision:
    build:
      context: ./services/vision
    restart: unless-stopped
    env_file: .env
    environment:
      API_BASE_URL: http://api:8000
      MODEL_PATH: /models/yolo26n.pt
      EVENT_STORAGE_PATH: /data/events
    volumes:
      - model_data:/models
      - event_data:/data/events
    depends_on:
      api:
        condition: service_healthy
    networks: [internal]

  web:
    build:
      context: ./apps/web
    restart: unless-stopped
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL}
    ports:
      - "127.0.0.1:3000:3000"
    depends_on: [api]
    networks: [internal]

  mediamtx:
    image: bluenviron/mediamtx:1.21.1
    restart: unless-stopped
    profiles: ["video"]
    volumes:
      - ./infra/mediamtx/mediamtx.yml:/mediamtx.yml:ro
    ports:
      - "127.0.0.1:8889:8889"
      - "127.0.0.1:8888:8888"
      - "127.0.0.1:8189:8189/udp"
    networks: [internal]

volumes:
  postgres_data:
  model_data:
  event_data:

networks:
  internal:
    driver: bridge
```

This is a **base development configuration**, not a finished production deployment. The Dockerfiles and app code below are required before the services will build. The MediaMTX image tag and ports must be verified against the selected release and network requirements. WebRTC may need additional ICE/NAT configuration. Camera streams must be reachable from the vision container; Docker networking may require special configuration depending on where the cameras live.

### `services/backend/Dockerfile`

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `services/vision/Dockerfile`

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
CMD ["python", "-m", "app.main"]
```

### Web setup

Initialize `apps/web`:

```bash
npx create-next-app@15 . --typescript --tailwind --eslint --app --src-dir=false
```

Commit the generated lockfile and use a pinned, tested Next.js/React dependency set.

`apps/web/Dockerfile`:

```dockerfile
FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "start"]
```

### `.gitignore`

```text
.env
.env.*
!.env.example
__pycache__/
*.py[cod]
.venv/
node_modules/
.next/
dist/
coverage/
models/*.pt
storage/*
!storage/.gitkeep
*.db
*.log
```

## 8. Database design

Initial entities:

| Table | Purpose |
|---|---|
| `sites` | Physical site or location |
| `cameras` | Camera metadata, encrypted stream URL, enabled flag, status, last-seen timestamp |
| `detection_rules` | Camera-specific rule type, JSON configuration, enabled flag |
| `events` | Event type, timestamp, severity, confidence, review status, metadata |
| `event_evidence` | Snapshot location and event association |
| `detection_logs` | Optional per-detection history; use retention limits because it grows quickly |

For a multi-customer product, add `organizations` and `users`, plus organization ownership and role-based access controls for all relevant records.

### SQLAlchemy model starter

Place this in `services/backend/app/db/models.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index,
    Integer, JSON, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    name: Mapped[str] = mapped_column(String(120))
    stream_url_encrypted: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default="offline", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class DetectionRule(Base):
    __tablename__ = "detection_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    rule_type: Mapped[str] = mapped_column(String(50))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_camera_time", "camera_id", "occurred_at"),
        Index("ix_events_type_time", "event_type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("detection_rules.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="unreviewed", index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EventEvidence(Base):
    __tablename__ = "event_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    snapshot_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class DetectionLog(Base):
    __tablename__ = "detection_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    class_name: Mapped[str] = mapped_column(String(60))
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    bounding_box: Mapped[list] = mapped_column(JSON)
```

### Database session

`services/backend/app/db/session.py`:

```python
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine(
    os.environ["DATABASE_URL"],
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

Use Alembic migrations for schema changes. Do not rely on `Base.metadata.create_all()` as the production migration strategy.

## 9. API endpoints

| Endpoint | Methods | Purpose |
|---|---|---|
| `/health` | GET | API health |
| `/api/cameras` | GET, POST | List/register cameras |
| `/api/cameras/{id}` | GET, PATCH, DELETE | Manage camera |
| `/api/cameras/{id}/test` | POST | Test connectivity |
| `/api/cameras/{id}/status` | GET | Connection and processing status |
| `/api/rules` | GET, POST | List/create rules |
| `/api/events` | GET | Filter events |
| `/api/events/{id}` | GET, PATCH | Inspect/update event status |
| `/api/analytics/summary` | GET | Aggregate counts |
| `/api/analytics/timeseries` | GET | Counts by interval |
| `/api/ws` | WebSocket | Push events and status changes |

Validate camera inputs, encrypt credentials at rest, and never return RTSP passwords in API responses. The vision agent should use an authenticated local channel to fetch camera configuration and submit events.

Example event payload:

```json
{
  "camera_id": "cam_001",
  "event_type": "restricted_zone_entry",
  "occurred_at": "2026-10-14T10:24:00Z",
  "severity": "high",
  "confidence": 0.91,
  "object": {
    "class": "person",
    "track_id": 42
  },
  "zone_id": "zone_loading_bay",
  "evidence": {
    "snapshot_path": "events/2026/10/14/event_001.jpg"
  },
  "status": "unreviewed"
}
```

The confidence above is illustrative only. Detection confidence is not the same as confidence that an event is dangerous.

## 10. Computer vision pipeline

1. **Ingest:** connect to an authorized RTSP stream; reconnect on failure.
2. **Sample:** process frames at a configurable rate and use a bounded queue.
3. **Detect:** run a pretrained detector and obtain class, confidence, and bounding boxes.
4. **Track:** maintain temporary object IDs per camera session.
5. **Apply rules:** evaluate zone entry, line crossing, dwell time, and occupancy.
6. **Create events:** apply cooldowns, save a timestamp and evidence snapshot, and publish the event.
7. **Monitor health:** report stream state, last frame time, inference speed, and worker errors.

Start with observable rules rather than attempting to infer intent. A person detector does not establish that a person is a threat.

## 11. Starter detector prototype

Use a local video file first, then replace it with an authorized RTSP URL.

```python
import cv2
from ultralytics import YOLO

SOURCE = "sample.mp4"
model = YOLO("yolo26n.pt")
cap = cv2.VideoCapture(SOURCE)

if not cap.isOpened():
    raise RuntimeError("Could not open video source")

try:
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        results = model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=0.4,
            verbose=False,
        )

        annotated = results[0].plot()
        cv2.imshow("AI Vision Edge Prototype", annotated)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
finally:
    cap.release()
    cv2.destroyAllWindows()
```

This proves only the detection/tracking loop. Production code still needs reconnect handling, frame sampling, per-camera tracker state, event rules, bounded queues, health monitoring, and event persistence. Verify the selected model name and license for the exact installed package.

## 12. Dashboard screens

### Overview
- Connected/offline camera totals.
- Recent events and alerts requiring review.
- People/vehicle counts.
- Basic activity trends.

### Live monitoring
- One-, four-, or nine-camera grid.
- Detection overlays and temporary tracking IDs.
- Camera health indicators.
- Full-screen playback.

### Camera management
- Add/edit/disable camera.
- Test connection.
- Configure stream and inference settings.
- Draw zones and virtual lines.

### Events and alerts
- Filter by site, camera, event type, status, and time.
- View evidence snapshot.
- Acknowledge, resolve, and add notes.
- Maintain an audit trail.

### Analytics
- People and vehicle counts by time interval.
- Entry/exit counts.
- Occupancy trends.
- CSV export.

### Settings
- Users and roles.
- Notification configuration.
- Retention settings.
- System health.

Implement Overview, Camera Management, and Events first. Add live browser playback after the core worker and event pipeline work.

## 13. Six-week implementation roadmap

| Stage | Target | Deliverable |
|---|---|---|
| 1 | Days 1–3 | Read one RTSP feed or sample video reliably |
| 2 | Days 4–7 | Detection, tracking, annotated frames, FPS measurement |
| 3 | Week 2 | FastAPI, PostgreSQL, migrations, camera registry |
| 4 | Week 3 | Dashboard, event API, live status updates |
| 5 | Week 4 | Zone/line rules, snapshots, basic analytics |
| 6 | Week 5 | Multi-camera reliability and performance tests |
| 7 | Week 6 | Controlled site pilot, retention, permissions, audit logging |

These are planning estimates for one full-time developer, not guaranteed delivery dates.

## 14. Performance testing for four cameras

Begin with one stream, then test two, then four.

Record:

- Per-camera inference FPS and end-to-end event latency.
- CPU, RAM, temperature, and dropped frames.
- Queue depth and stream reconnection time.
- False-positive and missed-detection rates on representative footage.
- Duplicate alerts and stability over several hours.

A starting benchmark target is around 3 inference frames/second/camera using low-resolution substreams. Actual performance depends on hardware, codec, model, resolution, and scene complexity. Reduce sampling or input size if needed before purchasing hardware.

## 15. Security and operational requirements

- Encrypt camera credentials using authenticated encryption; keep the key outside the database.
- Do not expose RTSP ports publicly.
- Use HTTPS/WSS for remote access and authenticated operator sessions.
- Add least-privilege roles and audit important actions before a customer pilot.
- Set retention and deletion policies for snapshots and logs.
- Back up the database and test restoration.
- Keep model inference separate from consequential security decisions; require human review.
- Confirm local privacy/data-protection obligations for each deployment.
- Pin container images and Python/Node dependencies to tested versions.
- Do not expose internal service ports beyond the intended local interface.

## 16. First commands

Create the repository:

```bash
mkdir ai-vision-edge
cd ai-vision-edge
git init

mkdir -p apps/web
mkdir -p services/backend/app/{api/routes,core,db,schemas,services}
mkdir -p services/backend/migrations
mkdir -p services/vision/app
mkdir -p infra/mediamtx storage tests
```

After creating the environment file and required Dockerfiles:

```bash
docker compose config
docker compose build
docker compose up -d db api
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "ok"}
```

The backend `main.py` must implement `/health` before this check will pass. Start the vision worker and web service after their code is ready.

## 17. Development checklist

- [ ] Create repository and environment.
- [ ] Verify one authorized camera stream or local video.
- [ ] Build detection/tracking worker.
- [ ] Create database schema and Alembic migration.
- [ ] Implement camera registration and credential encryption.
- [ ] Implement event rules, snapshots, and cooldowns.
- [ ] Build overview, camera, and event screens.
- [ ] Add browser playback.
- [ ] Test two and then four cameras.
- [ ] Add authentication, retention, audit logs, backups, and secure deployment defaults.
- [ ] Verify detector/model licensing for commercial distribution.
- [ ] Run a controlled pilot and review false positives.

## Recommended first milestone

Get one authorized camera feed into the Python worker, run person/vehicle detection, and measure real performance on the hardware you already own. Then add persistent events and the dashboard. Avoid training a custom model or buying hardware until the initial measurements show what the system actually needs.
