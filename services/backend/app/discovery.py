"""Find IP cameras on the local network, so adding one is "pick it from a list".

Two complementary passes, standard library only:
- ONVIF WS-Discovery: a UDP multicast probe most IP cameras answer (name, model, ONVIF address).
- A quick TCP sweep of the local /24 networks for RTSP ports, which also finds cameras with
  ONVIF switched off. An RTSP OPTIONS/DESCRIBE request then hints at the brand.

With the camera's username and password, `onvif_streams` asks the camera itself for its exact
stream URLs (main stream and substream), so nobody has to look up brand-specific RTSP paths.

Only private, loopback and link-local addresses are ever contacted.
"""

import asyncio
import base64
import hashlib
import ipaddress
import logging
import os
import re
import select
import socket
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

log = logging.getLogger(__name__)

WS_DISCOVERY = ("239.255.255.250", 3702)
RTSP_PORTS = (554, 8554, 10554)
MAX_SUBNETS = 4

# Substream first: it's what the worker should analyse (low resolution = light).
BRAND_PATHS: dict[str, list[tuple[str, str]]] = {
    "hikvision": [("Substream", "/Streaming/Channels/102"), ("Main stream", "/Streaming/Channels/101")],
    "dahua": [("Substream", "/cam/realmonitor?channel=1&subtype=1"), ("Main stream", "/cam/realmonitor?channel=1&subtype=0")],
    "reolink": [("Substream", "/h264Preview_01_sub"), ("Main stream", "/h264Preview_01_main")],
    "tp-link": [("Substream", "/stream2"), ("Main stream", "/stream1")],
    "uniview": [("Substream", "/media/video2"), ("Main stream", "/media/video1")],
    "axis": [("Default", "/axis-media/media.amp")],
    "amcrest": [("Substream", "/cam/realmonitor?channel=1&subtype=1"), ("Main stream", "/cam/realmonitor?channel=1&subtype=0")],
    "mediamtx": [("Phone (Larix)", "/cam1")],
}
BRAND_HINTS = {  # lowercase text seen in ONVIF scopes / RTSP headers -> brand
    "hikvision": "hikvision", "hikvision-webs": "hikvision", "ip camera(": "hikvision",
    "dahua": "dahua", "login to": "dahua", "reolink": "reolink", "tp-link": "tp-link", "tapo": "tp-link",
    "uniview": "uniview", "axis": "axis", "amcrest": "amcrest", "mediamtx": "mediamtx",
}


@dataclass
class Device:
    ip: str
    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    onvif_url: str | None = None
    rtsp_ports: list[int] = field(default_factory=list)
    brand: str | None = None

    def suggestions(self) -> list[dict]:
        port = self.rtsp_ports[0] if self.rtsp_ports else 554
        host = self.ip if port == 554 else f"{self.ip}:{port}"
        paths = BRAND_PATHS.get(self.brand or "", [])
        if self.brand == "mediamtx":
            return [{"label": label, "url": f"rtsp://{host}{path}"} for label, path in paths]
        return [{"label": label, "url": f"rtsp://USER:PASSWORD@{host}{path}"} for label, path in paths]

    def out(self) -> dict:
        return {"ip": self.ip, "name": self.name, "manufacturer": self.manufacturer, "model": self.model,
                "onvif_url": self.onvif_url, "rtsp_ports": self.rtsp_ports, "brand": self.brand,
                "suggestions": self.suggestions()}


def is_local(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _guess_brand(*texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t).lower()
    for hint, brand in BRAND_HINTS.items():
        if hint in blob:
            return brand
    return None


# --- local networks -------------------------------------------------------------------------

def local_ipv4() -> list[str]:
    """This machine's private IPv4 addresses (Wi-Fi, Ethernet; not loopback)."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:  # the address used for the default route (no packet is sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in found
                  if ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_loopback)


def local_subnets() -> list[ipaddress.IPv4Network]:
    """The /24 around each local address. Real home/office camera networks are almost always /24,
    and capping the size keeps the sweep to a few seconds."""
    nets = []
    for ip in local_ipv4():
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        if net not in nets:
            nets.append(net)
    return nets[:MAX_SUBNETS]


# --- ONVIF WS-Discovery ---------------------------------------------------------------------

PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <e:Header><w:MessageID>uuid:{id}</w:MessageID><w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
 <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>
 <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>"""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_probe_match(data: bytes, sender: str) -> Device | None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    xaddrs = scopes = ""
    for el in root.iter():
        if _local_name(el.tag) == "XAddrs" and el.text:
            xaddrs = el.text
        elif _local_name(el.tag) == "Scopes" and el.text:
            scopes = el.text
    if not xaddrs:
        return None
    # Prefer the address on the sender's own IP (cameras often list several, some unreachable).
    urls = [u for u in xaddrs.split() if urlsplit(u).hostname and is_local(urlsplit(u).hostname)]
    url = next((u for u in urls if urlsplit(u).hostname == sender), urls[0] if urls else None)
    fields: dict[str, str] = {}
    for scope in scopes.split():
        m = re.match(r"onvif://www\.onvif\.org/(name|hardware|mfr|manufacturer)/(.+)", scope, re.I)
        if m:
            fields[m.group(1).lower()] = urllib.request.unquote(m.group(2)).replace("_", " ")
    manufacturer = fields.get("mfr") or fields.get("manufacturer")
    return Device(ip=sender, name=fields.get("name"), manufacturer=manufacturer, model=fields.get("hardware"),
                  onvif_url=url, brand=_guess_brand(scopes))


def ws_discover(timeout: float = 2.5) -> list[Device]:
    """Multicast a probe from every local interface (Windows only sends multicast on one
    interface by default) and collect the answers."""
    socks = []
    for ip in local_ipv4() or ["0.0.0.0"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if ip != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.bind((ip, 0))
            s.setblocking(False)
            s.sendto(PROBE.format(id=uuid.uuid4()).encode(), WS_DISCOVERY)
            socks.append(s)
        except OSError as exc:
            log.debug("WS-Discovery on %s failed: %s", ip, exc)
    found: dict[str, Device] = {}
    deadline = time.monotonic() + timeout
    try:
        while socks and (left := deadline - time.monotonic()) > 0:
            ready, _, _ = select.select(socks, [], [], left)
            for s in ready:
                try:
                    data, (sender, _) = s.recvfrom(65535)
                except OSError:
                    continue
                if is_local(sender) and (dev := parse_probe_match(data, sender)):
                    found.setdefault(sender, dev)
    finally:
        for s in socks:
            s.close()
    return list(found.values())


# --- RTSP sweep ---------------------------------------------------------------------------

async def _port_open(ip: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def _rtsp_banner(ip: str, port: int, timeout: float = 1.5) -> str:
    """Server header and auth realm from an unauthenticated DESCRIBE (both often name the brand)."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return ""
    try:
        writer.write(f"DESCRIBE rtsp://{ip}:{port}/ RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: AIVisionEdge\r\n\r\n".encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(2048), timeout)
        text = data.decode("latin-1", "replace")
        return " ".join(line for line in text.splitlines()
                        if line.lower().startswith(("server:", "www-authenticate:")))
    except (OSError, asyncio.TimeoutError):
        return ""
    finally:
        writer.close()


async def rtsp_sweep(subnets: list[ipaddress.IPv4Network], timeout: float = 0.6,
                     concurrency: int = 200) -> dict[str, tuple[list[int], str]]:
    """ip -> (open RTSP ports, banner)."""
    sem = asyncio.Semaphore(concurrency)
    own = set(local_ipv4())

    async def probe(ip: str, port: int):
        async with sem:
            return ip, port, await _port_open(ip, port, timeout)

    hosts = [str(h) for net in subnets for h in net.hosts()]
    results = await asyncio.gather(*(probe(ip, p) for ip in hosts for p in RTSP_PORTS))
    open_ports: dict[str, list[int]] = {}
    for ip, port, ok in results:
        if ok:
            open_ports.setdefault(ip, []).append(port)
    # This machine's own MediaMTX (the phone) is reached as 127.0.0.1 by the worker.
    banners = await asyncio.gather(*(_rtsp_banner(ip, ports[0]) for ip, ports in open_ports.items()))
    out = {}
    for (ip, ports), banner in zip(open_ports.items(), banners):
        out["127.0.0.1" if ip in own else ip] = (sorted(ports), banner)
    return out


async def discover(timeout: float = 2.5) -> dict:
    subnets = local_subnets()
    onvif_task = asyncio.to_thread(ws_discover, timeout)
    onvif, rtsp = await asyncio.gather(onvif_task, rtsp_sweep(subnets))
    devices: dict[str, Device] = {d.ip: d for d in onvif}
    for ip, (ports, banner) in rtsp.items():
        dev = devices.setdefault(ip, Device(ip=ip))
        dev.rtsp_ports = ports
        dev.brand = dev.brand or _guess_brand(banner, dev.manufacturer)
        if ip == "127.0.0.1" and not dev.name:
            dev.name, dev.brand = "This computer (MediaMTX)", dev.brand or "mediamtx"
    for dev in devices.values():
        dev.brand = dev.brand or _guess_brand(dev.manufacturer, dev.model, dev.name)
        if dev.onvif_url and not dev.rtsp_ports:
            dev.rtsp_ports = [554]
    ordered = sorted(devices.values(), key=lambda d: (d.ip != "127.0.0.1", ipaddress.ip_address(d.ip)))
    return {"subnets": [str(n) for n in subnets], "devices": [d.out() for d in ordered]}


# --- ONVIF stream lookup ------------------------------------------------------------------

class OnvifError(Exception):
    pass


NS = {"s": "http://www.w3.org/2003/05/soap-envelope", "tds": "http://www.onvif.org/ver10/device/wsdl",
      "trt": "http://www.onvif.org/ver10/media/wsdl", "tt": "http://www.onvif.org/ver10/schema"}


def _security_header(username: str, password: str, clock_offset: timedelta) -> str:
    nonce = os.urandom(16)
    created = (datetime.now(timezone.utc) + clock_offset).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    esc = lambda v: v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<s:Header><wsse:Security s:mustUnderstand="1" '
        'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
        'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"<wsse:UsernameToken><wsse:Username>{esc(username)}</wsse:Username>"
        '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</wsse:Password>"
        '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</wsse:Nonce><wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security></s:Header>"
    )


def _soap(url: str, body: str, header: str = "", timeout: float = 5.0) -> ET.Element:
    host = urlsplit(url).hostname or ""
    if not is_local(host):
        raise OnvifError("Only cameras on the local network can be contacted.")
    envelope = (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{NS["s"]}" '
                f'xmlns:tds="{NS["tds"]}" xmlns:trt="{NS["trt"]}" xmlns:tt="{NS["tt"]}">{header}<s:Body>{body}</s:Body></s:Envelope>')
    req = urllib.request.Request(url, data=envelope.encode(), method="POST",
                                 headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local address checked above)
            data = resp.read(1_000_000)
    except urllib.error.HTTPError as exc:  # SOAP faults often come as HTTP 400/500 with a body
        if exc.code == 401 or b"NotAuthorized" in (data := exc.read(1_000_000)):
            raise OnvifError("The camera rejected the username or password.") from None
        if b"Fault" not in data:
            raise OnvifError(f"The camera answered HTTP {exc.code}.") from None
    except (urllib.error.URLError, OSError) as exc:
        raise OnvifError(f"Couldn't reach the camera ({getattr(exc, 'reason', exc)}).") from None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise OnvifError("The camera sent an unreadable answer.") from None
    if root.find(".//s:Fault", NS) is not None:
        text = " ".join(t.strip() for t in root.find(".//s:Fault", NS).itertext() if t.strip())
        if "notauthorized" in text.lower().replace(" ", "") or "auth" in text.lower():
            raise OnvifError("The camera rejected the username or password.")
        raise OnvifError(f"The camera returned an error: {text[:200]}")
    return root


def _clock_offset(url: str) -> timedelta:
    """Cameras reject logins whose timestamp is off from *their* clock, and camera clocks are
    often wrong, so sign with the camera's time."""
    try:
        root = _soap(url, "<tds:GetSystemDateAndTime/>")
        utc = root.find(".//tt:UTCDateTime", NS)
        d, t = utc.find("tt:Date", NS), utc.find("tt:Time", NS)
        cam = datetime(int(d.findtext("tt:Year", namespaces=NS)), int(d.findtext("tt:Month", namespaces=NS)),
                       int(d.findtext("tt:Day", namespaces=NS)), int(t.findtext("tt:Hour", namespaces=NS)),
                       int(t.findtext("tt:Minute", namespaces=NS)), int(t.findtext("tt:Second", namespaces=NS)),
                       tzinfo=timezone.utc)
        return cam - datetime.now(timezone.utc)
    except (OnvifError, AttributeError, TypeError, ValueError):
        return timedelta(0)


def with_credentials(uri: str, username: str, password: str) -> str:
    parts = urlsplit(uri)
    host = parts.hostname or ""
    netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def onvif_streams(onvif_url: str, username: str, password: str) -> list[dict]:
    """The camera's own stream URLs, smallest resolution first (the substream to analyse)."""
    offset = _clock_offset(onvif_url)
    auth = lambda: _security_header(username, password, offset)
    media_url = onvif_url
    try:
        caps = _soap(onvif_url, "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>", auth())
        media_url = caps.findtext(".//tt:Media/tt:XAddr", default=onvif_url, namespaces=NS) or onvif_url
    except OnvifError as exc:
        if "username or password" in str(exc):
            raise
    # Some cameras advertise an address the API can't reach (another NIC): keep the host we used.
    m = urlsplit(media_url)
    if m.hostname != urlsplit(onvif_url).hostname:
        o = urlsplit(onvif_url)
        media_url = urlunsplit((o.scheme, o.netloc, m.path, m.query, ""))

    profiles = _soap(media_url, "<trt:GetProfiles/>", auth()).findall(".//trt:Profiles", NS)
    if not profiles:
        raise OnvifError("The camera reported no video profiles.")
    streams = []
    for p in profiles[:6]:
        token = p.get("token")
        res = p.find(".//tt:VideoEncoderConfiguration/tt:Resolution", NS)
        width = int(res.findtext("tt:Width", "0", NS)) if res is not None else 0
        height = int(res.findtext("tt:Height", "0", NS)) if res is not None else 0
        body = ("<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport>"
                f"<tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup><trt:ProfileToken>{token}"
                "</trt:ProfileToken></trt:GetStreamUri>")
        try:
            uri = _soap(media_url, body, auth()).findtext(".//tt:Uri", namespaces=NS)
        except OnvifError:
            continue
        if not uri or not uri.lower().startswith("rtsp"):
            continue
        streams.append({"name": p.findtext("tt:Name", token, NS), "width": width, "height": height,
                        "url": with_credentials(uri.strip(), username, password)})
    if not streams:
        raise OnvifError("The camera didn't give any RTSP stream addresses.")
    return sorted(streams, key=lambda s: (s["width"] * s["height"] or 10**9))
