"""Push notifications through ntfy (https://ntfy.sh or a self-hosted server).

Sends from a background thread so a slow or unreachable notification server never delays
event ingest. A per camera+rule cooldown keeps one busy rule from flooding the phone.
"""

import base64
import logging
import queue
import threading
import time
from pathlib import Path

import httpx

from app.actions import ntfy_actions, ntfy_actions_header
from app.crypto import decrypt

log = logging.getLogger(__name__)

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
PRIORITY = {"low": 2, "medium": 3, "high": 4}  # ntfy: 1 min .. 5 max
TAGS = {
    "zone_entry": "rotating_light", "line_crossing": "footprints", "loitering": "hourglass",
    "occupancy_exceeded": "busts_in_silhouette", "camera_offline": "warning", "camera_online": "white_check_mark",
    "camera_tamper": "no_entry_sign", "camera_tamper_cleared": "white_check_mark",
}
LABELS = {
    "zone_entry": "Zone entry", "line_crossing": "Line crossing", "loitering": "Loitering",
    "occupancy_exceeded": "Occupancy exceeded", "camera_offline": "Camera offline", "camera_online": "Camera back online",
    "camera_tamper": "Camera tampering", "camera_tamper_cleared": "Camera view back to normal",
}


def _header(value: str) -> str:
    """httpx only sends ASCII headers; ntfy accepts RFC 2047 encoded words for anything else."""
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode()).decode() + "?="


def build_message(event: dict, cfg: dict) -> dict:
    label = LABELS.get(event["event_type"], event["event_type"].replace("_", " ").capitalize())
    camera = event.get("camera_name") or f"camera {event['camera_id']}"
    parts = [f"{label} on {camera}"]
    if event.get("rule_name"):
        parts.append(f"rule: {event['rule_name']}")
    if event.get("object_class"):
        conf = f" ({round(event['confidence'] * 100)}%)" if event.get("confidence") else ""
        parts.append(f"{event['object_class']}{conf}")
    msg = {
        "title": f"{label} - {camera}",
        "message": " - ".join(parts),
        "priority": PRIORITY.get(event.get("severity", "medium"), 3),
        "tags": [TAGS.get(event["event_type"], "bell")],
    }
    if cfg.get("dashboard_url"):
        msg["click"] = f"{cfg['dashboard_url'].rstrip('/')}/events?open={event['id']}"
        if event["event_type"] not in ("camera_online", "camera_tamper_cleared"):
            msg["actions"] = ntfy_actions(event, cfg["dashboard_url"])
    return msg


def should_notify(event: dict, cfg: dict) -> bool:
    if not cfg.get("enabled") or not cfg.get("topic"):
        return False
    if event["event_type"] in ("camera_online", "camera_tamper_cleared"):
        return False  # the recovery is visible in the dashboard; don't wake anyone up for it
    return SEVERITY_RANK.get(event.get("severity", "medium"), 1) >= SEVERITY_RANK.get(cfg.get("min_severity", "high"), 2)


def send(cfg: dict, msg: dict, snapshot: Path | None = None, client: httpx.Client | None = None) -> None:
    """Publish one message. Raises httpx.HTTPError on failure."""
    server = cfg["server"].rstrip("/")
    headers = {}
    if cfg.get("token_encrypted"):
        headers["Authorization"] = f"Bearer {decrypt(cfg['token_encrypted'])}"
    own = client is None
    client = client or httpx.Client(timeout=15)
    try:
        if snapshot is not None and snapshot.is_file():
            # Attachments are sent as the request body, with the text in headers.
            headers.update({
                "Title": _header(msg["title"]), "Message": _header(msg["message"]),
                "Priority": str(msg["priority"]), "Tags": ",".join(msg["tags"]),
                "Filename": snapshot.name,
            })
            if msg.get("click"):
                headers["Click"] = msg["click"]
            if msg.get("actions"):
                headers["Actions"] = ntfy_actions_header(msg["actions"])
            r = client.put(f"{server}/{cfg['topic']}", content=snapshot.read_bytes(), headers=headers)
            if 400 <= r.status_code < 500:
                # e.g. the server has attachments disabled or a size limit: the alert matters
                # more than the picture, so fall back to a text-only message.
                log.warning("ntfy rejected the snapshot (%s %s); sending text only", r.status_code, r.text[:120])
                text_headers = {k: v for k, v in headers.items() if k == "Authorization"}
                r = client.post(server, json={"topic": cfg["topic"], **msg}, headers=text_headers)
        else:
            r = client.post(server, json={"topic": cfg["topic"], **msg}, headers=headers)
        r.raise_for_status()
    finally:
        if own:
            client.close()


class Notifier:
    def __init__(self, media_root: Path):
        self.media_root = media_root
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._last: dict[tuple, float] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="notifier", daemon=True)
        self._thread.start()

    def notify(self, event: dict, cfg: dict) -> bool:
        """Queue a notification if the event qualifies. Returns True if queued."""
        if not should_notify(event, cfg):
            return False
        key = (event["camera_id"], event.get("rule_id") or event["event_type"])
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < cfg.get("cooldown_seconds", 60):
                return False
            self._last[key] = now
        snapshot = None
        if cfg.get("include_snapshot") and event.get("snapshot_path"):
            path = (self.media_root / event["snapshot_path"]).resolve()
            snapshot = path if path.is_relative_to(self.media_root) else None
        try:
            self._queue.put_nowait((cfg, build_message(event, cfg), snapshot))
            return True
        except queue.Full:
            log.warning("notification queue full; dropping notification for event %s", event["id"])
            return False

    def notify_system(self, cfg: dict, title: str, message: str, priority: int = 4,
                      tags: list[str] | None = None) -> bool:
        """System health messages (worker down, disk full): sent whenever notifications are on,
        whatever the minimum severity, because nothing else will tell the owner."""
        if not cfg.get("enabled") or not cfg.get("topic"):
            return False
        msg = {"title": title, "message": message, "priority": priority, "tags": tags or ["warning"]}
        if cfg.get("dashboard_url"):
            msg["click"] = f"{cfg['dashboard_url'].rstrip('/')}/settings"
        try:
            self._queue.put_nowait((cfg, msg, None))
            return True
        except queue.Full:
            return False

    def _run(self) -> None:
        while True:
            cfg, msg, snapshot = self._queue.get()
            for attempt in range(3):
                try:
                    send(cfg, msg, snapshot)
                    break
                except httpx.HTTPStatusError as exc:
                    log.warning("ntfy refused the notification: %s", exc)
                    if exc.response.status_code < 500:
                        break  # bad topic/token etc.: retrying won't help
                    time.sleep(2 ** attempt)
                except httpx.HTTPError as exc:
                    log.warning("ntfy send failed (attempt %d): %s", attempt + 1, exc)
                    time.sleep(2 ** attempt)


def _make_default() -> Notifier:
    from app.config import settings

    return Notifier(settings.event_storage_path.resolve())


notifier = _make_default()
