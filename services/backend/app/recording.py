"""Continuous recording and IP-camera proxying through MediaMTX.

Every enabled camera gets a MediaMTX path:
- phone / fake cameras already publish to MediaMTX (rtsp://127.0.0.1:8554/<path>): that path;
- IP cameras get a proxy path "aiv<camera id>" that pulls the camera once and shares it with the
  vision worker, the dashboard's live video and the recorder (one connection to the camera).

Recording is MediaMTX writing fMP4 segments straight from the stream (no re-encoding, almost no
CPU) into storage/recordings/<path>/. Settings > Recording switches it on and sets how many days
to keep; on top of that `enforce_cap` deletes the oldest segments when the total exceeds the size
cap or the disk runs low. Playback is MediaMTX's playback server, proxied by our API so it
stays behind the dashboard login.

The sync runs every 30 s and after camera changes, so a MediaMTX restart (which forgets paths
added through its API) heals itself within half a minute.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.config import settings

log = logging.getLogger(__name__)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "mediamtx"}
PROXY_PREFIX = "aiv"
UNMANAGED = {"all_others", "all", "~^.*$"}  # catch-all entries from mediamtx.yml: never touched


class MediaMTXUnavailable(Exception):
    pass


@dataclass(frozen=True)
class CameraPath:
    name: str
    proxied: bool
    source: str | None  # pull URL for proxied cameras


def camera_path(camera_id: int, stream_url: str) -> CameraPath | None:
    """Which MediaMTX path carries this camera (None = it can't go through MediaMTX)."""
    parts = urlsplit(stream_url)
    if parts.scheme not in ("rtsp", "rtsps"):
        return None  # HTTP/HLS test feeds and files are read directly by the worker
    if parts.hostname in LOCAL_HOSTS and (parts.port or 554) == 8554:
        name = parts.path.strip("/")
        return CameraPath(name, False, None) if name else None
    return CameraPath(f"{PROXY_PREFIX}{camera_id}", True, stream_url)


class MediaMTX:
    def __init__(self) -> None:
        self.enabled = bool(settings.mediamtx_api_password)
        self._auth = ("aivision", settings.mediamtx_api_password)
        self._api = httpx.Client(base_url=settings.mediamtx_api_url, auth=self._auth, timeout=5)

    def _call(self, method: str, url: str, **kw) -> httpx.Response:
        if not self.enabled:
            raise MediaMTXUnavailable("MEDIAMTX_API_PASSWORD is not set")
        try:
            r = self._api.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise MediaMTXUnavailable(f"MediaMTX not reachable ({exc})") from None
        if r.status_code >= 400:
            raise MediaMTXUnavailable(f"MediaMTX {method} {url}: HTTP {r.status_code} {r.text[:200]}")
        return r

    # --- configuration ---------------------------------------------------------------------
    def config_paths(self) -> dict[str, dict]:
        items = self._call("GET", "/v3/config/paths/list", params={"itemsPerPage": 1000}).json().get("items", [])
        return {p["name"]: p for p in items}

    def sync(self, desired: dict[str, dict], record_days: int) -> dict:
        """Make MediaMTX's runtime path config match `desired` (name -> settings)."""
        # Only write when it differs: every config write makes MediaMTX reload, which every 30 s
        # made live video stutter. Compared with MediaMTX's own value, so a restarted MediaMTX
        # (back to the file's default) still gets the setting.
        wanted = f"{record_days}d"
        current = self._call("GET", "/v3/config/pathdefaults/get").json().get("recordDeleteAfter")
        if current != wanted:
            self._call("PATCH", "/v3/config/pathdefaults/patch", json={"recordDeleteAfter": wanted})
        existing = self.config_paths()
        added = changed = removed = 0
        for name, conf in desired.items():
            if name not in existing:
                self._call("POST", f"/v3/config/paths/add/{name}", json=conf)
                added += 1
            elif any(existing[name].get(k) != v for k, v in conf.items()):
                self._call("PATCH", f"/v3/config/paths/patch/{name}", json=conf)
                changed += 1
        for name in existing:
            if name not in desired and name not in UNMANAGED:
                self._call("DELETE", f"/v3/config/paths/delete/{name}")
                removed += 1
        return {"added": added, "changed": changed, "removed": removed}

    # --- playback -------------------------------------------------------------------------
    def list_recordings(self, path: str) -> list[dict]:
        if not self.enabled:
            raise MediaMTXUnavailable("MEDIAMTX_API_PASSWORD is not set")
        try:
            r = httpx.get(f"{settings.mediamtx_playback_url}/list", params={"path": path}, auth=self._auth, timeout=10)
        except httpx.HTTPError as exc:
            raise MediaMTXUnavailable(f"playback server not reachable ({exc})") from None
        if r.status_code == 404:
            return []  # nothing recorded on this path yet
        if r.status_code >= 400:
            raise MediaMTXUnavailable(f"playback list: HTTP {r.status_code}")
        return [{"start": s["start"], "duration": float(s["duration"])} for s in r.json()]

    def open_playback(self, path: str, start: str, duration: float) -> httpx.Response:
        """A streaming MP4 response for [start, start + duration]. The caller must close it."""
        client = httpx.Client(timeout=httpx.Timeout(10, read=60), auth=self._auth)
        req = client.build_request("GET", f"{settings.mediamtx_playback_url}/get",
                                   params={"path": path, "start": start, "duration": f"{duration:g}", "format": "mp4"})
        r = client.send(req, stream=True)
        r.extensions["owning_client"] = client
        return r


mediamtx = MediaMTX()


def desired_paths(cameras: list[tuple[int, str, bool]], record: bool) -> dict[str, dict]:
    """cameras: (id, decrypted stream URL, enabled)."""
    out: dict[str, dict] = {}
    for cam_id, url, enabled in cameras:
        cp = camera_path(cam_id, url)
        if cp is None or not enabled:
            continue
        conf: dict = {"record": record}
        if cp.proxied:
            conf.update(source=cp.source, sourceOnDemand=False, rtspTransport="tcp")
        out[cp.name] = conf
    return out


def worker_url(camera_id: int, stream_url: str) -> str:
    """What the vision worker should open: the MediaMTX proxy for IP cameras (when MediaMTX is
    configured), so the camera serves a single connection."""
    cp = camera_path(camera_id, stream_url)
    if cp is not None and cp.proxied and mediamtx.enabled:
        return f"rtsp://{settings.mediamtx_rtsp}/{cp.name}"
    return stream_url


# --- storage cap ----------------------------------------------------------------------------

def enforce_cap(root: Path, max_bytes: int, min_free_bytes: int = 5 * 1024**3) -> dict:
    """Delete the oldest segments until the recordings fit the cap and the disk keeps
    `min_free_bytes` free. The newest segment of each camera (still being written) is kept."""
    if not root.is_dir():
        return {"deleted": 0, "bytes": 0}
    segments = []
    for cam_dir in root.iterdir():
        files = sorted(cam_dir.glob("*.mp4")) if cam_dir.is_dir() else []
        segments += [(f.stat().st_mtime, f) for f in files[:-1]]  # skip the one being written
    total = sum(f.stat().st_size for d in root.iterdir() if d.is_dir() for f in d.glob("*.mp4"))
    free = shutil.disk_usage(root).free
    deleted = 0
    for _, f in sorted(segments):
        if total <= max_bytes and free >= min_free_bytes:
            break
        size = f.stat().st_size
        try:
            f.unlink()
        except OSError:
            continue
        total, free, deleted = total - size, free + size, deleted + 1
    if deleted:
        log.info("recordings over the cap: deleted %d oldest segment(s)", deleted)
    return {"deleted": deleted, "bytes": total}


def delete_recordings(path_name: str) -> int:
    """Remove a camera's recordings (when the camera is deleted, like its events)."""
    target = (settings.recordings_path / path_name).resolve()
    if not path_name or not target.is_relative_to(settings.recordings_path.resolve()) or not target.is_dir():
        return 0
    n = sum(1 for _ in target.glob("*.mp4"))
    shutil.rmtree(target, ignore_errors=True)
    log.info("deleted %d recording segment(s) of %s", n, path_name)
    return n


def recordings_size(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(e.stat().st_size for d in os.scandir(root) if d.is_dir() for e in os.scandir(d.path) if e.is_file())


_cap_check = {"next": 0.0}


def sync_recording() -> None:
    """MediaMTX paths (IP-camera proxies, recording on/off) to match the cameras and settings.
    Runs every 30 s from the API's watchdog loop, and right after camera or setting changes."""
    if not mediamtx.enabled:
        return
    import time

    from sqlalchemy import select

    from app.crypto import decrypt
    from app.db import SessionLocal
    from app.models import Camera
    from app.settings_store import get_settings

    with SessionLocal() as db:
        cfg = get_settings(db)
        cams = [(c.id, decrypt(c.stream_url_encrypted), c.enabled) for c in db.scalars(select(Camera))]
    try:
        changes = mediamtx.sync(desired_paths(cams, cfg["recording_enabled"]), cfg["recording_days"])
        if any(changes.values()):
            log.info("MediaMTX paths synced: %s", changes)
    except MediaMTXUnavailable as exc:
        log.warning("MediaMTX sync skipped: %s", exc)
    if time.monotonic() >= _cap_check["next"]:
        _cap_check["next"] = time.monotonic() + 600
        enforce_cap(settings.recordings_path, cfg["recording_max_gb"] * 1024**3)
