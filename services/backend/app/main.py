"""AI Vision Edge API.

    uv run alembic upgrade head
    uv run uvicorn app.main:app --reload --port 8000

Docs at http://localhost:8000/docs
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
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
