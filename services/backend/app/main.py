"""AI Vision Edge API.

    uv run alembic upgrade head
    uv run uvicorn app.main:app --reload --port 8000

Docs at http://localhost:8000/docs
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import REPO_ROOT, settings
from app.live import hub
from app.routes import analytics, cameras, events, internal

app = FastAPI(title="AI Vision Edge API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cameras.router)
app.include_router(events.router)
app.include_router(analytics.router)
app.include_router(internal.router)

settings.event_storage_path.mkdir(parents=True, exist_ok=True)
app.mount("/media/events", StaticFiles(directory=settings.event_storage_path), name="event-media")


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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/api/ws")
async def live_updates(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; clients don't need to send anything
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


# Production: the built dashboard (apps/web/dist) on the same origin. Mounted last so API
# routes win. In development use the Vite dev server instead (npm run dev).
WEB_DIST = REPO_ROOT / "apps" / "web" / "dist"
if WEB_DIST.is_dir():
    app.mount("/", SPAStaticFiles(directory=WEB_DIST, html=True), name="web")
