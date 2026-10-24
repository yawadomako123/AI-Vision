"""The add-camera wizard's backend: makers by hardware address, the RTSP login check, recorders."""

import hashlib
import re
import socketserver
import threading

import pytest

from app import discovery

ARP_WINDOWS = """
Interface: 192.168.1.20 --- 0x4
  Internet Address      Physical Address      Type
  192.168.1.1           5c-4d-bf-d3-5d-01     dynamic
  192.168.1.64          24-28-fd-11-22-33     dynamic
  192.168.1.70          8a-c7-d5-35-49-ea     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
"""


def test_parse_arp_skips_broadcast_multicast_and_randomised_addresses():
    table = discovery.parse_arp(ARP_WINDOWS)
    assert table == {"192.168.1.1": "5C:4D:BF:D3:5D:01", "192.168.1.64": "24:28:FD:11:22:33"}
    linux = "IP address HW type Flags HW address Mask Device\n10.0.0.5 0x1 0x2 24:28:fd:aa:bb:cc * eth0\n"
    assert discovery.parse_arp(linux) == {"10.0.0.5": "24:28:FD:AA:BB:CC"}


def test_vendor_of():
    assert discovery.vendor_of("24:28:FD:11:22:33") == "hikvision"
    assert discovery.vendor_of(None) is None
    assert discovery.vendor_of("02:00:00:00:00:01") is None


def _tplink_mac() -> str:
    oui = next(o for o, v in discovery.OUI_VENDORS.items() if v == "tp-link")
    return ":".join([oui[0:2], oui[2:4], oui[4:6], "00", "00", "01"])


def _cloud_mac(brand: str) -> str:
    oui = next(o for o, v in discovery.OUI_VENDORS.items() if v == brand)
    return ":".join([oui[0:2], oui[2:4], oui[4:6], "00", "00", "02"])


def test_classify_finds_hidden_cameras_and_ignores_other_devices():
    tplink = _tplink_mac()
    arp = {
        "192.168.1.64": "24:28:FD:11:22:33",  # Hikvision, RTSP open
        "192.168.1.65": "24:28:FD:11:22:34",  # Hikvision, nothing open (RTSP off / not activated)
        "192.168.1.80": tplink,               # Tapo camera before its camera account: ONVIF 2020 open
        "192.168.1.1": tplink,                # a TP-Link router: no camera ports
        "192.168.1.90": _cloud_mac("ring"),   # Ring doorbell
        "192.168.1.99": "5C:4D:BF:00:00:01",  # unknown maker, only port 8000 open (a dev server)
    }
    ports = {"192.168.1.64": [554, 8000], "192.168.1.80": [2020], "192.168.1.99": [8000]}
    devices = {"192.168.1.64": discovery.Device(ip="192.168.1.64", rtsp_ports=[554])}
    found = {d.ip: d for d in discovery.classify(devices, ports, arp)}
    assert set(found) == {"192.168.1.64", "192.168.1.65", "192.168.1.80", "192.168.1.90"}
    assert (found["192.168.1.64"].kind, found["192.168.1.64"].brand) == ("camera", "hikvision")
    assert (found["192.168.1.65"].kind, found["192.168.1.65"].brand) == ("hidden", "hikvision")
    assert (found["192.168.1.80"].kind, found["192.168.1.80"].brand) == ("hidden", "tp-link")
    assert (found["192.168.1.90"].kind, found["192.168.1.90"].brand) == ("cloud_only", "ring")
    out = found["192.168.1.80"].out()
    assert out["brand_name"] == "Tapo" and out["default_user"] == ""
    assert found["192.168.1.64"].out()["default_user"] == "admin"


def test_recorder_by_model_name():
    assert discovery.Device(ip="10.0.0.2", model="DS-7608NI-K2").recorder
    assert discovery.Device(ip="10.0.0.2", model="iDS-7204HQHI-M1/S").recorder
    assert discovery.Device(ip="10.0.0.2", name="DHI-NVR4104").recorder
    assert not discovery.Device(ip="10.0.0.2", model="DS-2CD1043G0-I").recorder


# --- a fake camera speaking RTSP with Digest login ---------------------------------------------

class FakeCamera(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, paths: set[str], user="admin", password="right", digest=True, close_after_401=False,
                 address=("127.0.0.1", 0)):
        super().__init__(address, _Handler)
        self.paths, self.user, self.password = paths, user, password
        self.digest, self.close_after_401 = digest, close_after_401
        self.failed_logins = 0
        self.lock = threading.Lock()
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server_address[1]


class _Handler(socketserver.StreamRequestHandler):
    REALM, NONCE = "IP Camera(C1234)", "abc123"

    def handle(self):
        srv: FakeCamera = self.server
        while True:
            lines = []
            while True:
                line = self.rfile.readline()
                if not line:
                    return
                if line in (b"\r\n", b"\n"):
                    break
                lines.append(line.decode().rstrip("\r\n"))
            method, url, _ = lines[0].split(" ")
            headers = {k.lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
            cseq = headers.get("cseq", "1")
            path = re.sub(r"^rtsp://[^/]+", "", url)
            auth = headers.get("authorization")
            if not auth:
                self._reply(401, cseq, srv)
                if srv.close_after_401:
                    return
                continue
            if not self._valid(auth, url, srv):
                with srv.lock:
                    srv.failed_logins += 1
                self._reply(401, cseq, srv)
                continue
            if path not in srv.paths:
                self._reply(404, cseq, srv)
                continue
            body = b"v=0\r\nm=video 0 RTP/AVP 96\r\n"
            self.wfile.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Type: application/sdp\r\n"
                             f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

    def _reply(self, code: int, cseq: str, srv: FakeCamera):
        reason = {401: "Unauthorized", 404: "Not Found"}[code]
        extra = ""
        if code == 401:
            extra = (f'WWW-Authenticate: Digest realm="{self.REALM}", nonce="{self.NONCE}", stale="FALSE"\r\n'
                     if srv.digest else f'WWW-Authenticate: Basic realm="{self.REALM}"\r\n')
        self.wfile.write(f"RTSP/1.0 {code} {reason}\r\nCSeq: {cseq}\r\n{extra}\r\n".encode())

    def _valid(self, auth: str, url: str, srv: FakeCamera) -> bool:
        if auth.startswith("Basic "):
            import base64
            return base64.b64decode(auth[6:]).decode() == f"{srv.user}:{srv.password}"
        p = dict(re.findall(r'(\w+)="?([^",]*)"?', auth[7:]))
        ha1 = hashlib.md5(f"{srv.user}:{self.REALM}:{srv.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"DESCRIBE:{p.get('uri')}".encode()).hexdigest()
        want = hashlib.md5(f"{ha1}:{self.NONCE}:{ha2}".encode()).hexdigest()
        return p.get("username") == srv.user and p.get("response") == want and p.get("uri") == url


@pytest.fixture
def recorder():
    # A Hikvision NVR with cameras on channels 1, 2, 3 and 5.
    cam = FakeCamera({f"/Streaming/Channels/{n}02" for n in (1, 2, 3, 5)})
    yield cam
    cam.shutdown()
    cam.server_close()


def test_rtsp_status_digest_login(recorder):
    base = f"rtsp://admin:right@127.0.0.1:{recorder.port}"
    assert discovery.rtsp_status(f"{base}/Streaming/Channels/102") == 200
    assert discovery.rtsp_status(f"{base}/Streaming/Channels/902") == 404
    assert discovery.rtsp_status(f"rtsp://admin:wrong@127.0.0.1:{recorder.port}/Streaming/Channels/102") == 401
    assert discovery.rtsp_status("rtsp://admin:x@127.0.0.1:1/x") == 0
    with pytest.raises(ValueError):
        discovery.rtsp_status("rtsp://admin:x@8.8.8.8/x")


def test_connect_finds_every_camera_of_a_recorder(recorder):
    result = discovery.connect("127.0.0.1", "hikvision", "admin", "right", port=recorder.port)
    assert result["recorder"] is True
    assert [s["channel"] for s in result["streams"]] == [1, 2, 3, 5]
    assert result["streams"][1]["url"] == f"rtsp://admin:right@127.0.0.1:{recorder.port}/Streaming/Channels/202"
    assert recorder.failed_logins == 0


def test_connect_wrong_password_tries_once_and_explains(recorder):
    with pytest.raises(discovery.ConnectError) as err:
        discovery.connect("127.0.0.1", "hikvision", "admin", "wrong", port=recorder.port)
    assert err.value.code == "wrong_password" and "30 minutes" in str(err.value)
    assert recorder.failed_logins == 1  # never more than one wrong login (cameras lock themselves)


def test_connect_tapo_basic_auth_and_reconnect_after_401():
    cam = FakeCamera({"/stream2"}, user="tapouser", password="pw@1:x", digest=False, close_after_401=True)
    try:
        result = discovery.connect("127.0.0.1", "tp-link", "tapouser", "pw@1:x", port=cam.port)
        assert result == {"streams": [{"channel": 1, "url": f"rtsp://tapouser:pw%401%3Ax@127.0.0.1:{cam.port}/stream2"}],
                          "recorder": False}
        with pytest.raises(discovery.ConnectError, match="camera account"):
            discovery.connect("127.0.0.1", "tp-link", "tapouser", "nope", port=cam.port)
    finally:
        cam.shutdown()
        cam.server_close()


def test_connect_unknown_brand_tries_common_addresses_and_stops_at_a_refusal():
    cam = FakeCamera({"/h264Preview_01_sub"})
    try:
        result = discovery.connect("127.0.0.1", None, "admin", "right", port=cam.port)
        assert result["streams"][0]["url"].endswith("/h264Preview_01_sub")
        with pytest.raises(discovery.ConnectError) as err:
            discovery.connect("127.0.0.1", None, "admin", "wrong", port=cam.port)
        assert err.value.code == "wrong_password" and cam.failed_logins == 1
    finally:
        cam.shutdown()
        cam.server_close()


def test_connect_unreachable_and_not_local():
    with pytest.raises(discovery.ConnectError) as err:
        discovery.connect("127.0.0.1", "hikvision", "admin", "x", port=1)
    assert err.value.code == "unreachable"
    with pytest.raises(discovery.ConnectError) as err:
        discovery.connect("8.8.8.8", "hikvision", "admin", "x")
    assert err.value.code == "not_local"


# --- the API --------------------------------------------------------------------------------------

def test_connect_endpoint(client, operator, recorder):
    body = {"ip": "127.0.0.1", "brand": "hikvision", "username": "admin", "password": "right", "port": recorder.port}
    r = client.post("/api/cameras/discover/connect", json=body)
    assert r.status_code == 200 and r.json()["ok"] and len(r.json()["streams"]) == 4
    r = client.post("/api/cameras/discover/connect", json={**body, "password": "Zq9-not-it"})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["code"] == "wrong_password"
    assert "Zq9-not-it" not in str(client.get("/api/audit").json())  # the password is never logged
    assert operator.post("/api/cameras/discover/connect", json=body).status_code == 403


def test_snapshot_endpoint_only_contacts_local_addresses(client):
    r = client.post("/api/cameras/discover/snapshot", json={"stream_url": "rtsp://user:pw@8.8.8.8/stream"})
    assert r.status_code == 400
    r = client.post("/api/cameras/discover/snapshot", json={"stream_url": "rtsp://user:pw@127.0.0.1:1/stream"})
    assert r.status_code == 502 and "pw" not in r.json()["detail"]


def test_virtual_and_vpn_adapters_are_not_searched():
    netsh = '''
Configuration for interface "Tailscale"
    IP Address:                           100.115.129.106
Configuration for interface "Wi-Fi"
    IP Address:                           192.168.0.49
Configuration for interface "vEthernet (Default Switch)"
    IP Address:                           172.22.48.1
'''
    adapters = discovery.parse_adapters(netsh)
    assert adapters == {"100.115.129.106": "Tailscale", "192.168.0.49": "Wi-Fi", "172.22.48.1": "vEthernet (Default Switch)"}
    assert [ip for ip, n in adapters.items() if discovery.VIRTUAL_ADAPTER.search(n)] == ["100.115.129.106", "172.22.48.1"]
    linux = "2: eth0    inet 192.168.1.5/24 brd 192.168.1.255 scope global eth0\n3: docker0    inet 172.17.0.1/16 scope global docker0\n"
    assert {ip for ip, n in discovery.parse_adapters(linux).items() if discovery.VIRTUAL_ADAPTER.search(n)} == {"172.17.0.1"}


def test_phone_setup_gives_the_next_free_slot_and_a_larix_link(client, operator, monkeypatch):
    from urllib.parse import unquote

    from app.routes import cameras as routes
    monkeypatch.setattr(routes.app_settings, "mediamtx_publish_password", "p@ss/word")
    monkeypatch.setattr(routes.discovery, "primary_ipv4", lambda: "192.168.1.20")
    monkeypatch.setattr(routes.discovery, "network_category", lambda ip: "Public")
    client.post("/api/cameras", json={"name": "Phone 1", "stream_url": "rtsp://127.0.0.1:8554/cam1"})
    r = client.get("/api/cameras/phone-setup")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == "cam2" and body["publish_url"] == "rtsp://192.168.1.20:8554/cam2"
    assert body["read_url"] == "rtsp://127.0.0.1:8554/cam2" and body["username"] == "publisher"
    link = body["larix_url"]
    assert link.startswith("larix://set/v1?conn[][url]=rtsp%3A%2F%2F192.168.1.20%3A8554%2Fcam2&")
    assert "conn[][pass]=p%40ss%2Fword" in link and unquote(link).count("p@ss/word") == 1
    assert "enc[vid][res]=640x480" in link
    assert body["network_public"] is True  # the dashboard then explains the Private network setting
    assert operator.get("/api/cameras/phone-setup").status_code == 403


def test_phone_setup_without_a_network_explains(client, monkeypatch):
    from app.routes import cameras as routes
    monkeypatch.setattr(routes.app_settings, "mediamtx_publish_password", "x")
    monkeypatch.setattr(routes.discovery, "primary_ipv4", lambda: None)
    r = client.get("/api/cameras/phone-setup")
    assert r.status_code == 409 and "network" in r.json()["detail"]
