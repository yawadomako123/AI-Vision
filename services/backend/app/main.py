"""AI Vision Edge API.

    uv run alembic upgrade head
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

Docs at http://127.0.0.1:8000/docs
"""

import asyncio
import json
import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import current_user, websocket_user
from app.config import REPO_ROOT, settings
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.live import hub, worker_contact
from app.settings_store import get_settings
from app.watchdog import watchdog
from app.recording import sync_recording
from app import daily_summary
from app.notifier import notifier
from app.models import User
from app.retention import run_retention
from app.routes import actions, analytics, auth, cameras, events, internal, recordings, rules, sightings, users

log = logging.getLogger(__name__)
RETENTION_INTERVAL_SECONDS = 3600
WATCHDOG_INTERVAL_SECONDS = 30


def _retention_once() -> None:
    with SessionLocal() as db:
        run_retention(db, settings.event_storage_path)


async def _retention_loop() -> None:
    while True:
        try:
            await run_in_threadpool(_retention_once)
        except Exception:  # noqa: BLE001 - never let housekeeping kill the API
            log.exception("retention run failed")
        await asyncio.sleep(RETENTION_INTERVAL_SECONDS)


def _watchdog_once() -> None:
    with SessionLocal() as db:
        cfg = get_settings(db, "notifications")
        watchdog.check(settings.event_storage_path, worker_contact.get("at"), cfg)
        daily_summary.maybe_send(db, notifier, watchdog.status())
    sync_recording()  # also heals MediaMTX after a restart (it forgets API-added paths)


async def _watchdog_loop() -> None:
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
        try:
            await run_in_threadpool(_watchdog_once)
        except Exception:  # noqa: BLE001
            log.exception("watchdog check failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = [asyncio.create_task(_retention_loop()), asyncio.create_task(_watchdog_loop())] \
        if settings.run_retention else []
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="AI Vision Edge API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, users, cameras, rules, events, analytics, sightings, internal, actions, recordings):
    app.include_router(module.router)

settings.event_storage_path.mkdir(parents=True, exist_ok=True)
MEDIA_ROOT = settings.event_storage_path.resolve()


@app.get("/media/events/{path:path}", include_in_schema=False)
def event_media(path: str, _: User = Depends(current_user)):
    """Snapshots are evidence: only logged-in operators may fetch them."""
    file = (MEDIA_ROOT / path).resolve()
    if not file.is_relative_to(MEDIA_ROOT) or not file.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return FileResponse(file, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/api/ws")
async def live_updates(ws: WebSocket, db: Session = Depends(get_db)):
    user = await run_in_threadpool(websocket_user, ws, db)
    db.close()  # only needed for the handshake
    if user is None:
        await ws.close(code=4401)  # app-defined: not logged in
        return
    await hub.connect(ws)
    try:
        while True:
            # Keep-alive, plus {"type": "watch", "cameras": [ids]} from the Live page.
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
                if msg.get("type") == "watch":
                    hub.watch(ws, {int(c) for c in msg.get("cameras", [])[:16]})
            except (ValueError, TypeError, AttributeError):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


# Windows' MIME registry doesn't know the web app manifest type.
mimetypes.add_type("application/manifest+json", ".webmanifest")


class SPAStaticFiles(StaticFiles):
    """Serves the built dashboard; unknown non-API paths get index.html (client-side routes)."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # scope["path"], not `path`: the latter is OS-normalized (backslashes on Windows).
            if exc.status_code == 404 and not scope["path"].startswith(("/api/", "/media/")):
                return await super().get_response("index.html", scope)
            raise


# Production: the built dashboard (apps/web/dist) on the same origin. Mounted last so API
# routes win. In development use the Vite dev server instead (npm run dev).
WEB_DIST = settings.web_dist or (REPO_ROOT / "apps" / "web" / "dist")
if WEB_DIST.is_dir():
    app.mount("/", SPAStaticFiles(directory=WEB_DIST, html=True), name="web")
