"""Live updates: pushes events and camera status changes to connected dashboards.

The Live page also tells the hub which cameras it shows ("watch"). The worker only sends
detection boxes and preview images for watched cameras, so the live wall costs nothing
while nobody has it open."""

import asyncio
import json
import logging
import time

import anyio.from_thread
from fastapi import WebSocket

log = logging.getLogger(__name__)


class LiveHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._watching: dict[WebSocket, set[int]] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        self._watching.pop(ws, None)

    def watch(self, ws: WebSocket, camera_ids: set[int]) -> None:
        if camera_ids:
            self._watching[ws] = camera_ids
        else:
            self._watching.pop(ws, None)

    def watched(self) -> set[int]:
        return set().union(*self._watching.values()) if self._watching else set()

    async def send_to_watchers(self, camera_id: int, kind: str, data: dict) -> None:
        message = json.dumps({"type": kind, "data": data}, default=str)
        for ws, cams in list(self._watching.items()):
            if camera_id in cams:
                try:
                    await asyncio.wait_for(ws.send_text(message), timeout=1)
                except Exception:  # noqa: BLE001
                    self.disconnect(ws)

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

# Newest preview JPEG per camera for the live wall: camera id -> (jpeg, unix time). In memory
# only: it's a convenience view, not evidence, and is gone a few seconds after watching stops.
live_previews: dict[int, tuple[bytes, float]] = {}
PREVIEW_MAX_AGE = 10.0


def latest_preview(camera_id: int) -> bytes | None:
    item = live_previews.get(camera_id)
    if item is None or time.time() - item[1] > PREVIEW_MAX_AGE:
        return None
    return item[0]


# Last time the vision worker talked to the API (system health). Process-local is fine:
# there is one API process per edge box.
worker_contact: dict = {}
