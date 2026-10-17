import base64
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import discovery

PROBE_MATCH = b"""<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><s:Body><d:ProbeMatches><d:ProbeMatch>
 <d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/name/HIKVISION%20DS-2CD1043
 onvif://www.onvif.org/hardware/DS-2CD1043G0-I onvif://www.onvif.org/location/city/hangzhou</d:Scopes>
 <d:XAddrs>http://169.254.1.5/onvif/device_service http://192.168.1.64/onvif/device_service</d:XAddrs>
 </d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"""


def test_parse_probe_match_prefers_sender_address_and_reads_scopes():
    dev = discovery.parse_probe_match(PROBE_MATCH, "192.168.1.64")
    assert dev.onvif_url == "http://192.168.1.64/onvif/device_service"
    assert dev.name == "HIKVISION DS-2CD1043" and dev.model == "DS-2CD1043G0-I"
    assert dev.brand == "hikvision"
    assert dev.suggestions()[0] == {"label": "Substream", "url": "rtsp://USER:PASSWORD@192.168.1.64/Streaming/Channels/102"}
    assert discovery.parse_probe_match(b"<not xml", "192.168.1.9") is None


def test_brand_from_rtsp_banner_and_port_in_suggestion():
    assert discovery._guess_brand('WWW-Authenticate: Digest realm="Login to 4L0B3E7PAZ"') == "dahua"
    dev = discovery.Device(ip="10.0.0.7", rtsp_ports=[8554], brand="reolink")
    assert dev.suggestions()[0]["url"] == "rtsp://USER:PASSWORD@10.0.0.7:8554/h264Preview_01_sub"
    assert discovery.Device(ip="10.0.0.8").suggestions() == []  # unknown brand: no guesses


def test_only_local_addresses():
    assert discovery.is_local("192.168.1.10") and discovery.is_local("127.0.0.1") and discovery.is_local("169.254.3.3")
    assert not discovery.is_local("8.8.8.8") and not discovery.is_local("example.com")
    with pytest.raises(discovery.OnvifError, match="local network"):
        discovery.onvif_streams("http://8.8.8.8/onvif/device_service", "u", "p")


def test_credentials_are_url_encoded():
    assert discovery.with_credentials("rtsp://192.168.1.64:554/Streaming/Channels/102", "admin", "p@ss:w/rd") == \
        "rtsp://admin:p%40ss%3Aw%2Frd@192.168.1.64:554/Streaming/Channels/102"


class FakeCamera(BaseHTTPRequestHandler):
    """Just enough ONVIF: checks the WS-Security digest like a real camera does."""

    password = "secret"

    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: str):
        data = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
                f'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">'
                f"<s:Body>{body}</s:Body></s:Envelope>").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/soap+xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, req: str) -> bool:
        import re
        try:
            digest = re.search(r"PasswordDigest\">([^<]+)<", req).group(1)
            nonce = base64.b64decode(re.search(r"Base64Binary\">([^<]+)<", req).group(1))
            created = re.search(r"<wsu:Created>([^<]+)<", req).group(1)
        except AttributeError:
            return False
        return digest == base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()

    def do_POST(self):
        req = self.rfile.read(int(self.headers["Content-Length"])).decode()
        if "GetSystemDateAndTime" in req:
            return self._reply(200, "<tt:UTCDateTime><tt:Time><tt:Hour>1</tt:Hour><tt:Minute>2</tt:Minute>"
                                    "<tt:Second>3</tt:Second></tt:Time><tt:Date><tt:Year>2026</tt:Year>"
                                    "<tt:Month>10</tt:Month><tt:Day>17</tt:Day></tt:Date></tt:UTCDateTime>")
        if not self._authorized(req):
            return self._reply(400, "<s:Fault><s:Code><s:Value>s:Sender</s:Value></s:Code>"
                                    "<s:Reason><s:Text>ter:NotAuthorized</s:Text></s:Reason></s:Fault>")
        if "GetCapabilities" in req:
            return self._reply(200, "<tt:Media><tt:XAddr>http://10.9.9.9/onvif/media</tt:XAddr></tt:Media>")
        if "GetProfiles" in req:
            prof = ('<trt:Profiles token="{t}"><tt:Name>{n}</tt:Name><tt:VideoEncoderConfiguration><tt:Resolution>'
                    "<tt:Width>{w}</tt:Width><tt:Height>{h}</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration></trt:Profiles>")
            return self._reply(200, prof.format(t="main", n="mainStream", w=1920, h=1080)
                               + prof.format(t="sub", n="subStream", w=640, h=360))
        if "GetStreamUri" in req:
            ch = "102" if ">sub<" in req else "101"
            return self._reply(200, f"<tt:Uri>rtsp://127.0.0.1:554/Streaming/Channels/{ch}</tt:Uri>")
        self._reply(500, "<s:Fault><s:Reason><s:Text>unknown</s:Text></s:Reason></s:Fault>")


@pytest.fixture
def fake_camera():
    server = HTTPServer(("127.0.0.1", 0), FakeCamera)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/onvif/device_service"
    server.shutdown()


def test_onvif_streams_substream_first(fake_camera):
    streams = discovery.onvif_streams(fake_camera, "admin", "secret")
    assert [(s["name"], s["width"]) for s in streams] == [("subStream", 640), ("mainStream", 1920)]
    assert streams[0]["url"] == "rtsp://admin:secret@127.0.0.1:554/Streaming/Channels/102"


def test_onvif_wrong_password(fake_camera):
    with pytest.raises(discovery.OnvifError, match="username or password"):
        discovery.onvif_streams(fake_camera, "admin", "nope")


def test_endpoints(client, operator, fake_camera, monkeypatch):
    async def fake_discover():
        return {"subnets": ["192.168.1.0/24"], "devices": [discovery.Device(ip="192.168.1.64", brand="hikvision").out()]}
    monkeypatch.setattr(discovery, "discover", fake_discover)
    client.post("/api/cameras", json={"name": "Hall", "stream_url": "rtsp://u:p@192.168.1.64/Streaming/Channels/102"})

    r = client.post("/api/cameras/discover")
    assert r.status_code == 200 and r.json()["devices"][0]["already_added"] is True
    assert operator.post("/api/cameras/discover").status_code == 403  # admins only

    r = client.post("/api/cameras/discover/onvif", json={"onvif_url": fake_camera, "username": "admin", "password": "secret"})
    assert r.status_code == 200 and len(r.json()["streams"]) == 2
    r = client.post("/api/cameras/discover/onvif", json={"onvif_url": fake_camera, "username": "admin", "password": "x"})
    assert r.status_code == 502 and "password" in r.json()["detail"]

    assert client.post("/api/cameras/probe", json={"stream_url": "file:///etc/passwd"}).status_code == 422
    r = client.post("/api/cameras/probe", json={"stream_url": "rtsp://127.0.0.1:9/nothing"}).json()
    assert r["ok"] is False and "127.0.0.1:9/nothing" not in (r["error"] or "")
