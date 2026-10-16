"""Live updates: pushes events and camera status changes to connected dashboards."""

import asyncio
import json
import logging

import anyio.from_thread
from fastapi import WebSocket

log = logging.getLogger(__name__)


class LiveHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, kind: str, data: dict) -> None:
        message = json.dumps({"type": kind, "data": data}, default=str)
        dead = []
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=2)
            except Exception:  # noqa: BLE001 - a slow or closed client must not block others
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_from_sync(self, kind: str, data: dict) -> None:
        """Call from sync route handlers (they run in AnyIO worker threads)."""
        try:
            anyio.from_thread.run(self.broadcast, kind, data)
        except RuntimeError:
            log.debug("no event loop for broadcast (e.g. in tests); skipped")


hub = LiveHub()
