"""Find IP cameras on the local network, so adding one is "pick it from a list".

Three complementary passes, standard library only:
- ONVIF WS-Discovery: a UDP multicast probe most IP cameras answer (name, model, ONVIF address).
- A quick TCP sweep of the local /24 networks for RTSP ports, which also finds cameras with
  ONVIF switched off. An RTSP DESCRIBE request then hints at the brand.
- The maker behind each device's hardware (MAC) address, from this computer's ARP table (the
  sweep fills it). This recognises cameras that answer neither, e.g. a Tapo camera before its
  "camera account" is set up, so the dashboard can say exactly what to switch on.

`connect` then turns an address plus the camera's password into ready-to-add streams: it checks
the password with one RTSP request, finds every camera on a recorder (NVR/DVR), and explains
failures in plain words. `onvif_streams` asks an ONVIF camera for its own stream URLs.

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
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from app.camera_vendors import OUI_VENDORS

log = logging.getLogger(__name__)

WS_DISCOVERY = ("239.255.255.250", 3702)
RTSP_PORTS = (554, 8554, 10554)
# Not video, but they give a camera away when its RTSP is off: Tapo's ONVIF port, Hikvision's SDK port.
HINT_PORTS = (2020, 8000)
MAX_SUBNETS = 4
MAX_CHANNELS = 16  # cameras looked for on a recorder (NVR/DVR)

BRAND_NAMES = {
    "hikvision": "Hikvision", "ezviz": "EZVIZ", "tp-link": "Tapo", "dahua": "Dahua", "reolink": "Reolink",
    "uniview": "Uniview", "axis": "Axis", "amcrest": "Amcrest", "hanwha": "Hanwha", "lorex": "Lorex",
    "mediamtx": "Phone",
}
# These only stream through their maker's cloud: they can't be used, and saying so saves a long search.
CLOUD_ONLY = {"ring": "Ring", "arlo": "Arlo", "blink": "Blink", "wyze": "Wyze", "nest": "Google Nest"}
# Login name most cameras of a brand use ("" = the customer creates one, e.g. Tapo's camera account).
DEFAULT_USER = {"hikvision": "admin", "ezviz": "admin", "dahua": "admin", "amcrest": "admin", "reolink": "admin",
                "uniview": "admin", "hanwha": "admin", "lorex": "admin", "axis": "root", "tp-link": ""}

# Substream first: it's what the worker should analyse (low resolution = light).
BRAND_PATHS: dict[str, list[tuple[str, str]]] = {
    "hikvision": [("Substream", "/Streaming/Channels/102"), ("Main stream", "/Streaming/Channels/101")],
    "dahua": [("Substream", "/cam/realmonitor?channel=1&subtype=1"), ("Main stream", "/cam/realmonitor?channel=1&subtype=0")],
    "reolink": [("Substream", "/h264Preview_01_sub"), ("Main stream", "/h264Preview_01_main")],
    "tp-link": [("Substream", "/stream2"), ("Main stream", "/stream1")],
    "ezviz": [("Substream", "/h264/ch1/sub/av_stream"), ("Main stream", "/h264/ch1/main/av_stream")],
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
    mac: str | None = None
    # camera: video reachable | hidden: a camera maker's device with its video switched off |
    # cloud_only: works only through its maker's cloud | phone: this computer's MediaMTX
    kind: str = "camera"

    @property
    def recorder(self) -> bool:
        """A Hikvision/Dahua-style recorder by its model name (DS-7xxx/9xxx, NVR/DVR/XVR)."""
        text = f"{self.model or ''} {self.name or ''}"
        return bool(re.search(r"\bi?DS-[79]\d|\b[NDX]VR\b|\bNVR\d|\bDHI-[NX]VR", text, re.I))

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
                "brand_name": BRAND_NAMES.get(self.brand or "") or CLOUD_ONLY.get(self.brand or ""),
                "mac": self.mac, "kind": self.kind, "recorder": self.recorder,
                "default_user": DEFAULT_USER.get(self.brand or "", "admin"),
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

# Virtual and VPN adapters: no cameras live there, and Hyper-V/WSL switches even answer for
# addresses that don't exist, which would show up as phantom cameras.
VIRTUAL_ADAPTER = re.compile(r"vEthernet|Hyper-V|WSL|VirtualBox|VMware|Docker|Loopback|Tailscale|ZeroTier|"
                             r"Bluetooth|WireGuard|OpenVPN|TAP-|^docker|^br-|^veth|^virbr|^lxc|^tailscale|^zt|^wg",
                             re.I)


def parse_adapters(text: str) -> dict[str, str]:
    """ip -> adapter name, from `netsh interface ip show address` (Windows) or `ip -o -4 addr` (Linux)."""
    out: dict[str, str] = {}
    name = ""
    for line in text.splitlines():
        if m := re.match(r'Configuration for interface "(.+)"', line.strip()):
            name = m.group(1)
        elif m := re.match(r"IP Address:\s*(\d+\.\d+\.\d+\.\d+)", line.strip()):
            out[m.group(1)] = name
        elif m := re.match(r"\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", line.strip()):
            out[m.group(2)] = m.group(1)
    return out


def _virtual_ips() -> set[str]:
    cmd = ["netsh", "interface", "ip", "show", "address"] if os.name == "nt" else ["ip", "-o", "-4", "addr"]
    try:
        text = subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {ip for ip, name in parse_adapters(text).items() if VIRTUAL_ADAPTER.search(name)}


def local_ipv4() -> list[str]:
    """This machine's private IPv4 addresses on real networks (Wi-Fi, Ethernet; not loopback,
    virtual switches or VPNs)."""
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
    virtual = _virtual_ips()
    return sorted(ip for ip in found
                  if ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_loopback
                  and ip not in virtual)


def primary_ipv4() -> str | None:
    """The address other devices on the network use to reach this computer (the default route's)."""
    ips = local_ipv4()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            mine = s.getsockname()[0]
        if mine in ips:
            return mine
    except OSError:
        pass
    return ips[0] if ips else None


def network_category(ip: str) -> str | None:
    """Windows' profile for the network this address is on: "Public", "Private" or "DomainAuthenticated".
    Phones and cameras can only connect in on Private networks (the installer's firewall rule)."""
    if os.name != "nt" or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip):
        return None
    cmd = (f"(Get-NetIPAddress -IPAddress {ip} -ErrorAction Stop | Get-NetConnectionProfile -ErrorAction Stop)"
           ".NetworkCategory")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                             text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if value in ("Public", "Private", "DomainAuthenticated") else None


def local_subnets() -> list[ipaddress.IPv4Network]:
    """The /24 around each local address. Real home/office camera networks are almost always /24,
    and capping the size keeps the sweep to a few seconds."""
    nets = []
    for ip in local_ipv4():
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        if net not in nets:
            nets.append(net)
    return nets[:MAX_SUBNETS]


# --- makers by hardware address -------------------------------------------------------------

_ARP_LINE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b.*?\b([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})\b")


def parse_arp(text: str) -> dict[str, str]:
    """ip -> MAC from `arp -a` (Windows) or /proc/net/arp (Linux)."""
    out = {}
    for line in text.splitlines():
        m = _ARP_LINE.search(line)
        if not m:
            continue
        ip, mac = m.group(1), m.group(2).replace("-", ":").upper()
        first = int(mac[:2], 16)
        # Skip broadcast/multicast and randomised ("private") addresses: phones use those, cameras don't.
        if first & 0b11 or not is_local(ip) or ipaddress.ip_address(ip).is_loopback:
            continue
        out[ip] = mac
    return out


def arp_table() -> dict[str, str]:
    try:
        if os.name == "nt":
            text = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        else:
            text = Path("/proc/net/arp").read_text()
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_arp(text)


def vendor_of(mac: str | None) -> str | None:
    return OUI_VENDORS.get(mac.replace(":", "")[:6]) if mac else None


def locate_mac(near_ip: str, port: int, mac: str) -> str | None:
    """Where a device with this hardware address is now, on the /24 around its old address.
    Knocking on every address fills the ARP table (even where the port is closed)."""
    net = ipaddress.ip_network(f"{near_ip}/24", strict=False)

    async def knock() -> None:
        sem = asyncio.Semaphore(200)

        async def one(ip: str) -> None:
            async with sem:
                await _port_open(ip, port, 0.6)

        await asyncio.gather(*(one(str(h)) for h in net.hosts()))

    asyncio.run(knock())
    return next((ip for ip, m in arp_table().items()
                 if m == mac.upper() and ipaddress.ip_address(ip) in net), None)


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
    """Status line, Server header and auth realm from an unauthenticated DESCRIBE (the last two often
    name the brand). Empty if nothing speaks RTSP there."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return ""
    try:
        writer.write(f"DESCRIBE rtsp://{ip}:{port}/ RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: AIVisionEdge\r\n\r\n".encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(2048), timeout)
        text = data.decode("latin-1", "replace")
        if not text.startswith("RTSP/"):
            return ""
        return " ".join(line for i, line in enumerate(text.splitlines())
                        if i == 0 or line.lower().startswith(("server:", "www-authenticate:")))
    except (OSError, asyncio.TimeoutError):
        return ""
    finally:
        writer.close()


async def rtsp_sweep(subnets: list[ipaddress.IPv4Network], timeout: float = 0.6,
                     concurrency: int = 400) -> dict[str, tuple[list[int], str]]:
    """ip -> (open RTSP and hint ports, RTSP banner). Connecting to every address also fills the
    ARP table that `arp_table` reads, even for devices with every port closed."""
    sem = asyncio.Semaphore(concurrency)
    own = set(local_ipv4())

    async def probe(ip: str, port: int):
        async with sem:
            return ip, port, await _port_open(ip, port, timeout)

    hosts = [str(h) for net in subnets for h in net.hosts()]
    results = await asyncio.gather(*(probe(ip, p) for ip in hosts for p in RTSP_PORTS + HINT_PORTS))
    open_ports: dict[str, list[int]] = {}
    for ip, port, ok in results:
        if ok:
            open_ports.setdefault(ip, []).append(port)

    async def banner(ip: str, ports: list[int]) -> str:
        rtsp = [p for p in ports if p in RTSP_PORTS]
        return await _rtsp_banner(ip, rtsp[0]) if rtsp else ""

    banners = await asyncio.gather(*(banner(ip, ports) for ip, ports in open_ports.items()))
    out = {}
    for (ip, ports), text in zip(open_ports.items(), banners):
        # This machine's own MediaMTX (the phone) is reached as 127.0.0.1 by the worker.
        out["127.0.0.1" if ip in own else ip] = (sorted(ports), text)
    return out


def classify(devices: dict[str, Device], ports: dict[str, list[int]], arp: dict[str, str]) -> list[Device]:
    """Decide what each found address is, add camera-maker devices only the ARP table knows about,
    and drop everything that isn't a camera."""
    for ip, mac in arp.items():
        if vendor_of(mac) and ip not in devices:
            devices[ip] = Device(ip=ip)
    kept = []
    for ip, dev in devices.items():
        open_ = ports.get(ip, [])
        dev.mac = dev.mac or arp.get(ip)
        vendor = vendor_of(dev.mac)
        dev.rtsp_ports = dev.rtsp_ports or [p for p in open_ if p in RTSP_PORTS]
        dev.brand = dev.brand or _guess_brand(dev.manufacturer, dev.model, dev.name) or vendor
        if ip == "127.0.0.1":
            dev.kind = "phone"
        elif vendor in CLOUD_ONLY:
            dev.kind, dev.brand = "cloud_only", vendor
        elif dev.rtsp_ports or dev.onvif_url:
            dev.kind = "camera"
        elif vendor == "tp-link" and 2020 not in open_:
            continue  # a TP-Link router, plug or bulb: Tapo cameras keep their ONVIF port 2020 open
        elif vendor:
            dev.kind = "hidden"
        else:
            continue  # only a hint port open on an unknown device: not evidence of a camera
        if dev.onvif_url and not dev.rtsp_ports:
            dev.rtsp_ports = [554]
        kept.append(dev)
    return kept


async def discover(timeout: float = 2.5) -> dict:
    subnets = local_subnets()
    onvif_task = asyncio.to_thread(ws_discover, timeout)
    onvif, swept = await asyncio.gather(onvif_task, rtsp_sweep(subnets))
    arp = await asyncio.to_thread(arp_table)
    devices: dict[str, Device] = {d.ip: d for d in onvif}
    for ip, (ports, banner) in swept.items():
        rtsp = [p for p in ports if p in RTSP_PORTS] if banner else []  # an open port alone isn't a camera
        if not rtsp and ip not in devices and not vendor_of(arp.get(ip)):
            continue
        dev = devices.setdefault(ip, Device(ip=ip))
        dev.rtsp_ports = rtsp
        dev.brand = dev.brand or _guess_brand(banner, dev.manufacturer)
        if ip == "127.0.0.1" and not dev.name:
            dev.name, dev.brand = "This computer (MediaMTX)", dev.brand or "mediamtx"
    # RTSP ports count only where something answered in RTSP; hint ports count as they are.
    verified = {ip: [p for p in ports if p not in RTSP_PORTS or banner] for ip, (ports, banner) in swept.items()}
    found = classify(devices, verified, arp)
    order = {"phone": 0, "camera": 1, "hidden": 2, "cloud_only": 3}
    found.sort(key=lambda d: (order[d.kind], ipaddress.ip_address(d.ip)))
    return {"subnets": [str(n) for n in subnets], "devices": [d.out() for d in found]}


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


# --- connecting: password check, recorder channels -------------------------------------------

def _rtsp_request(sock: socket.socket, url: str, cseq: int, auth: str | None) -> tuple[int, list[tuple[str, str]]]:
    lines = [f"DESCRIBE {url} RTSP/1.0", f"CSeq: {cseq}", "Accept: application/sdp", "User-Agent: AIVisionEdge"]
    if auth:
        lines.append(f"Authorization: {auth}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    text = head.decode("latin-1", "replace").splitlines()
    m = re.match(r"RTSP/\d\.\d\s+(\d{3})", text[0] if text else "")
    headers = [(k.strip().lower(), v.strip()) for k, _, v in (line.partition(":") for line in text[1:])]
    length = next((int(v) for k, v in headers if k == "content-length" and v.isdigit()), 0)
    while len(body) < length:  # read the body so a second request on this connection lines up
        chunk = sock.recv(min(65536, length - len(body)))
        if not chunk:
            break
        body += chunk
    return (int(m.group(1)) if m else 0), headers


def _rtsp_auth(headers: list[tuple[str, str]], url: str, user: str, password: str) -> str | None:
    """Authorization header for a 401's challenge: Digest when offered (most cameras), else Basic."""
    challenges = [v for k, v in headers if k == "www-authenticate"]
    digest = next((c for c in challenges if c.lower().startswith("digest")), None)
    if digest:
        p = dict(re.findall(r'(\w+)="?([^",]*)"?', digest[6:]))
        ha1 = hashlib.md5(f"{user}:{p.get('realm', '')}:{password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"DESCRIBE:{url}".encode()).hexdigest()
        nonce = p.get("nonce", "")
        fields = f'username="{user}", realm="{p.get("realm", "")}", nonce="{nonce}", uri="{url}"'
        if "auth" in p.get("qop", "").split(","):
            cnonce = os.urandom(8).hex()
            resp = hashlib.md5(f"{ha1}:{nonce}:00000001:{cnonce}:auth:{ha2}".encode()).hexdigest()
            fields += f', qop=auth, nc=00000001, cnonce="{cnonce}"'
        else:
            resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        if p.get("opaque"):
            fields += f', opaque="{p["opaque"]}"'
        return f'Digest {fields}, response="{resp}"'
    if any(c.lower().startswith("basic") for c in challenges):
        return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    return None


def rtsp_status(url: str, timeout: float = 4.0) -> int:
    """RTSP status of a stream URL, logging in with the credentials in it: 200 = it works,
    401 = wrong username/password, 404 = no such stream, 0 = unreachable. At most one login
    attempt (cameras lock themselves after several wrong passwords)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not is_local(host):
        raise ValueError("Only cameras on the local network can be contacted.")
    user, password = unquote(parts.username or ""), unquote(parts.password or "")
    clean = urlunsplit((parts.scheme, host + (f":{parts.port}" if parts.port else ""), parts.path, parts.query, ""))
    auth = None
    try:
        with socket.create_connection((host, parts.port or 554), timeout) as sock:
            sock.settimeout(timeout)
            code, headers = _rtsp_request(sock, clean, 1, None)
            if code != 401 or not (user or password):
                return code
            auth = _rtsp_auth(headers, clean, user, password)
            if auth is None:
                return code
            try:
                code = _rtsp_request(sock, clean, 2, auth)[0]
                if code:
                    return code
            except OSError:
                pass
        # Some cameras close the connection after a 401: send the login on a new one.
        with socket.create_connection((host, parts.port or 554), timeout) as sock:
            sock.settimeout(timeout)
            return _rtsp_request(sock, clean, 1, auth)[0]
    except OSError:
        return 0


# Substream first everywhere: it's the light one the AI should analyse.
CHANNEL_PATHS = {  # brands whose recorders number their cameras
    "hikvision": "/Streaming/Channels/{n}02",
    "dahua": "/cam/realmonitor?channel={n}&subtype=1",
    "amcrest": "/cam/realmonitor?channel={n}&subtype=1",
}
SINGLE_PATHS = {
    "tp-link": "/stream2", "ezviz": "/h264/ch1/sub/av_stream", "reolink": "/h264Preview_01_sub",
    "uniview": "/media/video2", "axis": "/axis-media/media.amp",
}
# Unknown brand: the common substream addresses, most likely first.
GENERIC_PATHS = ["/Streaming/Channels/102", "/stream2", "/cam/realmonitor?channel=1&subtype=1",
                 "/h264Preview_01_sub", "/media/video2", "/h264/ch1/sub/av_stream", "/live/ch00_1", "/"]

WRONG_LOGIN = {
    "hikvision": "Wrong username or password. Check them carefully before trying again: Hikvision cameras lock "
                 "logins for 30 minutes after a few wrong tries. A brand-new camera must be activated first "
                 "(see the Hikvision help below).",
    "ezviz": "Wrong password. For EZVIZ cameras the username is admin and the password is the 6-letter "
             "verification code on the camera's sticker.",
    "tp-link": "Wrong camera account. Use the camera account you created in the Tapo app (camera settings → "
               "Advanced settings → Camera account), not your TP-Link/Tapo app login.",
}


class ConnectError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _wrong_login(brand: str | None) -> ConnectError:
    return ConnectError("wrong_password", WRONG_LOGIN.get(brand or "", "Wrong username or password for this camera."))


def connect(ip: str, brand: str | None, username: str, password: str, port: int = 554,
            onvif_url: str | None = None) -> dict:
    """Turn a found device plus its login into ready-to-add stream URLs (substreams).
    Returns {"streams": [{"channel": n, "url": ...}], "recorder": bool}."""
    if not is_local(ip):
        raise ConnectError("not_local", "Only cameras on the local network can be added this way.")
    host = ip if port == 554 else f"{ip}:{port}"

    def url(path: str) -> str:
        if brand == "mediamtx":  # this computer's camera hub: reading needs no login
            return f"rtsp://{host}{path}"
        return with_credentials(f"rtsp://{host}{path}", username, password)

    def found(paths: list[tuple[int, str]]) -> dict:
        return {"streams": [{"channel": n, "url": url(p)} for n, p in paths], "recorder": len(paths) > 1}

    code = -1
    if brand == "mediamtx":  # phones streaming to this computer: every slot that has video right now
        slots = [f"/cam{n}" for n in range(1, MAX_CHANNELS + 1)]
        with ThreadPoolExecutor(8) as pool:
            codes = list(pool.map(lambda p: rtsp_status(url(p)), slots))
        live = [(n, p) for n, (p, c) in enumerate(zip(slots, codes), start=1) if c == 200]
        if live:
            return {"streams": [{"channel": n, "url": url(p)} for n, p in live], "recorder": False}
        raise ConnectError("no_stream", "No phone is streaming yet. In Larix, tap the round record button, "
                           "then try again.")
    if brand in CHANNEL_PATHS:
        template = CHANNEL_PATHS[brand]
        code = rtsp_status(url(template.format(n=1)))
        if code in (401, 403):
            raise _wrong_login(brand)
        if code == 200:
            # The login works: look for a recorder's other cameras, all at once.
            others = list(range(2, MAX_CHANNELS + 1))
            with ThreadPoolExecutor(8) as pool:
                codes = list(pool.map(lambda n: rtsp_status(url(template.format(n=n))), others))
            return found([(1, template.format(n=1))] +
                         [(n, template.format(n=n)) for n, c in zip(others, codes) if c == 200])
    elif brand in SINGLE_PATHS:
        code = rtsp_status(url(SINGLE_PATHS[brand]))
        if code in (401, 403):
            raise _wrong_login(brand)
        if code == 200:
            return found([(1, SINGLE_PATHS[brand])])

    if onvif_url:  # the camera's own answer, whatever its brand
        try:
            streams = onvif_streams(onvif_url, username, password)
            return {"streams": [{"channel": 1, "url": streams[0]["url"]}], "recorder": False}
        except OnvifError as exc:
            if "username or password" in str(exc):
                raise _wrong_login(brand) from None

    if brand not in CHANNEL_PATHS and brand not in SINGLE_PATHS:
        for path in GENERIC_PATHS:
            code = rtsp_status(url(path))
            if code == 200:
                return found([(1, path)])
            if code in (401, 403):  # stop at the first refusal: never guess
                raise ConnectError("wrong_password", "Wrong username or password, or this camera uses a stream "
                                   "address we don't know. Check the login, or add it by hand with its RTSP address.")
            if code == 0:
                break
    if code == 0:
        raise ConnectError("unreachable", "Couldn't reach the camera. Check it's switched on and connected, "
                           "then search again.")
    raise ConnectError("no_stream", "The camera answered but we couldn't find its video. Add it by hand with "
                       "its RTSP address (in the camera's manual or app).")
