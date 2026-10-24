"""Keep cameras connected when the router gives them a new address.

Home routers hand out addresses (DHCP). After a power cut a camera can come back on a different
one, and to a non-technical owner it simply looks "offline" for good. A camera's hardware (MAC)
address never changes, so:
- learn: while a camera is online, remember the MAC behind its address (from the ARP table);
- heal: once a camera has been offline for a couple of minutes, look for its MAC on its network.
  If it's on a new address and its video works there, switch the camera over (audit-logged).

Runs from the API's 30 s watchdog loop. Only local network addresses are ever contacted.
"""

import ipaddress
import logging
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import discovery
from app.audit import audit
from app.crypto import decrypt, encrypt
from app.models import Camera

log = logging.getLogger(__name__)

OFFLINE_FOR = 120.0   # seconds offline before looking for the camera elsewhere
RETRY_EVERY = 600.0   # per camera, so a camera that's really off doesn't cause a sweep every 30 s


def lan_host(url: str) -> str | None:
    """The camera's IP if the stream is an RTSP address on the local network (not this computer)."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("rtsp", "rtsps") or not parts.hostname:
        return None
    try:
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return None
    return parts.hostname if discovery.is_local(parts.hostname) and not ip.is_loopback else None


def with_host(url: str, host: str) -> str:
    """The same stream URL (login, port, path) on another address."""
    parts = urlsplit(url)
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = (f"{userinfo}@" if userinfo else "") + host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class CameraFinder:
    def __init__(self, arp: Callable[[], dict[str, str]] = discovery.arp_table,
                 locate: Callable[[str, int, str], str | None] = discovery.locate_mac,
                 check: Callable[[str], int] = discovery.rtsp_status, clock: Callable[[], float] = time.time):
        self.arp, self.locate, self.check, self.clock = arp, locate, check, clock
        self.last_try: dict[int, float] = {}

    def run(self, db: Session) -> list[tuple[str, str, str]]:
        """Learn MACs and move cameras that changed address. Returns (name, old IP, new IP) for each move."""
        now = self.clock()
        arp: dict[str, str] | None = None
        moved: list[tuple[str, str, str]] = []
        changed = False
        for cam in db.scalars(select(Camera).where(Camera.enabled)):
            url = decrypt(cam.stream_url_encrypted)
            host = lan_host(url)
            if host is None:
                continue
            if cam.status == "online":
                if not cam.mac:
                    arp = arp if arp is not None else self.arp()
                    if mac := arp.get(host):
                        cam.mac, changed = mac, True
                continue
            if cam.status != "offline" or not cam.mac:
                continue
            last: datetime | None = cam.last_seen_at
            if last is not None and now - last.timestamp() < OFFLINE_FOR:
                continue
            if now - self.last_try.get(cam.id, 0.0) < RETRY_EVERY:
                continue
            self.last_try[cam.id] = now
            if self.arp().get(host) == cam.mac:
                continue  # still on the same address: the camera itself is off or unplugged
            new = self.locate(host, urlsplit(url).port or 554, cam.mac)
            if not new or new == host:
                continue
            new_url = with_host(url, new)
            if self.check(new_url) != 200:
                log.info("camera %s seen at %s but its video doesn't answer there yet", cam.name, new)
                continue
            cam.stream_url_encrypted = encrypt(new_url)
            audit(db, None, "system", "camera.readdressed", f"camera:{cam.name}", old=host, new=new)
            log.warning("camera %s moved from %s to %s: reconnected automatically", cam.name, host, new)
            moved.append((cam.name, host, new))
            changed = True
        if changed:
            db.commit()
        return moved


camera_finder = CameraFinder()
