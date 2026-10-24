"""Cameras that get a new address from the router are followed automatically."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.camera_finder import CameraFinder, lan_host, with_host
from app.crypto import decrypt, encrypt
from app.models import AuditLog, Camera

MAC = "24:28:FD:11:22:33"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def test_url_helpers():
    assert lan_host("rtsp://admin:p%40ss@192.168.1.64:554/Streaming/Channels/102") == "192.168.1.64"
    assert lan_host("rtsp://127.0.0.1:8554/cam1") is None          # this computer's camera hub
    assert lan_host("https://example.com/live.m3u8") is None       # not a local RTSP camera
    assert lan_host("rtsp://cam.local/stream") is None             # a name, not an address
    assert with_host("rtsp://admin:p%40ss@192.168.1.64:554/Streaming/Channels/102", "192.168.1.71") == \
        "rtsp://admin:p%40ss@192.168.1.71:554/Streaming/Channels/102"
    assert with_host("rtsp://192.168.1.64/stream2?x=1", "192.168.1.9") == "rtsp://192.168.1.9/stream2?x=1"


class Net:
    """A pretend network: where each MAC is, and which stream URLs answer."""

    def __init__(self, arp: dict[str, str], working: set[str]):
        self.arp, self.working = arp, working
        self.sweeps = 0

    def table(self) -> dict[str, str]:
        return dict(self.arp)

    def locate(self, near: str, port: int, mac: str) -> str | None:
        self.sweeps += 1
        return next((ip for ip, m in self.arp.items() if m == mac), None)

    def check(self, url: str) -> int:
        return 200 if url in self.working else 0


def _camera(db_session, status: str, mac: str | None, url: str, offline_for: float = 600) -> int:
    with db_session() as db:
        cam = Camera(name="Gate", stream_url_encrypted=encrypt(url), status=status, mac=mac,
                     last_seen_at=NOW - timedelta(seconds=offline_for))
        db.add(cam)
        db.commit()
        return cam.id


def _finder(net: Net, at: datetime = NOW) -> CameraFinder:
    return CameraFinder(arp=net.table, locate=net.locate, check=net.check, clock=lambda: at.timestamp())


def test_learns_the_mac_of_an_online_camera(db_session):
    cam_id = _camera(db_session, "online", None, "rtsp://admin:pw@192.168.1.64/Streaming/Channels/102")
    with db_session() as db:
        assert _finder(Net({"192.168.1.64": MAC}, set())).run(db) == []
        assert db.get(Camera, cam_id).mac == MAC


def test_follows_a_camera_to_its_new_address(db_session):
    old = "rtsp://admin:pw@192.168.1.64:554/Streaming/Channels/102"
    new = "rtsp://admin:pw@192.168.1.71:554/Streaming/Channels/102"
    cam_id = _camera(db_session, "offline", MAC, old)
    net = Net({"192.168.1.71": MAC}, {new})
    finder = _finder(net)
    with db_session() as db:
        assert finder.run(db) == [("Gate", "192.168.1.64", "192.168.1.71")]
        assert decrypt(db.get(Camera, cam_id).stream_url_encrypted) == new
        log = db.scalars(select(AuditLog).where(AuditLog.action == "camera.readdressed")).one()
        assert log.username == "system" and log.detail == {"old": "192.168.1.64", "new": "192.168.1.71"}
        assert "pw" not in str(log.detail)


def test_leaves_cameras_alone_when_nothing_moved(db_session):
    url = "rtsp://admin:pw@192.168.1.64/Streaming/Channels/102"
    _camera(db_session, "offline", MAC, url)
    net = Net({"192.168.1.64": MAC}, set())  # same address: the camera is just switched off
    with db_session() as db:
        assert _finder(net).run(db) == [] and net.sweeps == 0


def test_waits_before_looking_and_doesnt_sweep_every_30_seconds(db_session):
    _camera(db_session, "offline", MAC, "rtsp://admin:pw@192.168.1.64/s", offline_for=30)
    net = Net({}, set())
    finder = _finder(net)
    with db_session() as db:
        finder.run(db)
        assert net.sweeps == 0  # only 30 s offline: could be a reboot
    late = _finder(net, NOW + timedelta(minutes=5))
    with db_session() as db:
        late.run(db)
        late.run(db)
        assert net.sweeps == 1  # gone (not found anywhere): tried once, then not again for 10 minutes


def test_doesnt_switch_when_the_video_doesnt_answer_at_the_new_address(db_session):
    old = "rtsp://admin:pw@192.168.1.64/s"
    cam_id = _camera(db_session, "offline", MAC, old)
    with db_session() as db:
        assert _finder(Net({"192.168.1.80": MAC}, set())).run(db) == []
        assert decrypt(db.get(Camera, cam_id).stream_url_encrypted) == old


def test_create_camera_stores_the_mac_from_the_wizard(client):
    r = client.post("/api/cameras", json={"name": "Drive", "stream_url": "rtsp://a:b@192.168.1.5/s",
                                          "mac": "24:28:fd:aa:bb:cc"})
    assert r.status_code == 201
    r = client.post("/api/cameras", json={"name": "Bad", "stream_url": "rtsp://a:b@192.168.1.6/s", "mac": "nope"})
    assert r.status_code == 422
