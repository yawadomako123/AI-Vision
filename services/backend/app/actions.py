"""Signed one-tap actions for phone notifications (ntfy action buttons).

Each button carries a token that allows exactly one thing (acknowledge event 42, or mute camera
3's notifications for an hour) and expires 2 hours after the alert. Tokens are HMAC-signed with
a key derived from the server's SERVICE_TOKEN, so they can't be forged or edited.

Anyone who can read the notification topic can press the buttons, so they're deliberately
limited: "mute" only silences phone notifications for that camera for 1 hour; detection,
recording and the dashboard keep working. Every use is audited.
"""

import base64
import hashlib
import hmac
import json
import time

from app.config import settings

TTL_SECONDS = 2 * 3600
MUTE_MINUTES = 60
ACTIONS = {"ack", "mute"}


def _key() -> bytes:
    return hashlib.sha256(f"notification-actions:{settings.service_token}".encode()).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(action: str, target_id: int, now: float | None = None) -> str:
    assert action in ACTIONS
    body = _b64(json.dumps({"a": action, "id": target_id, "exp": int((now or time.time()) + TTL_SECONDS)},
                           separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest()[:18])
    return f"{body}.{sig}"


class InvalidToken(Exception):
    pass


class ExpiredToken(Exception):
    pass


def read_token(token: str, now: float | None = None) -> tuple[str, int]:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest()[:18])
        if not hmac.compare_digest(sig, expected):
            raise InvalidToken
        data = json.loads(_unb64(body))
        action, target = data["a"], int(data["id"])
        expires = data["exp"]
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidToken from exc
    if action not in ACTIONS:
        raise InvalidToken
    if (now or time.time()) > expires:
        raise ExpiredToken
    return action, target


def ntfy_actions(event: dict, dashboard_url: str) -> list[dict]:
    """Buttons for one alert. The phone calls the dashboard address directly (e.g. over
    Tailscale), so there are no buttons without a dashboard link."""
    if not dashboard_url:
        return []
    base = dashboard_url.rstrip("/")
    button = lambda label, token: {
        "action": "http", "label": label, "url": f"{base}/api/actions/{token}", "method": "POST",
        "headers": {"X-Requested-With": "aivision"}, "clear": True,
    }
    return [button("Acknowledge", make_token("ack", event["id"])),
            button(f"Mute camera {MUTE_MINUTES // 60} h", make_token("mute", event["camera_id"]))]


def ntfy_actions_header(actions: list[dict]) -> str:
    """The same buttons in ntfy's header format (used when a snapshot is attached)."""
    return "; ".join(
        f"http, {a['label']}, {a['url']}, method=POST, headers.X-Requested-With=aivision, clear=true"
        for a in actions)
