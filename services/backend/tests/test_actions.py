import time

from app import actions
from app.notifier import build_message
from tests.conftest import WORKER
from tests.test_api import event_payload

CSRF = {"X-Requested-With": "aivision"}


def new_event(client, camera, ext="a1"):
    return client.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"], ext_id=ext)).json()


def test_tokens_are_signed_scoped_and_expire():
    t = actions.make_token("ack", 42)
    assert actions.read_token(t) == ("ack", 42)
    body, sig = t.split(".")
    forged = actions._b64(b'{"a":"ack","id":43,"exp":9999999999}') + "." + sig  # edited target
    for bad in (forged, body + ".AAAA", "nonsense", ""):
        try:
            actions.read_token(bad)
            raise AssertionError(f"accepted {bad!r}")
        except actions.InvalidToken:
            pass
    old = actions.make_token("mute", 1, now=time.time() - actions.TTL_SECONDS - 5)
    try:
        actions.read_token(old)
        raise AssertionError("expired token accepted")
    except actions.ExpiredToken:
        pass


def test_acknowledge_button(anon, client, camera):
    ev = new_event(client, camera)
    url = f"/api/actions/{actions.make_token('ack', ev['id'])}"
    assert anon.post(url, headers={"X-Requested-With": ""}).status_code == 403  # no CSRF header: a web page can't trigger it
    r = anon.post(url, headers=CSRF)
    assert r.status_code == 200 and r.json()["ok"]
    e = client.get(f"/api/events/{ev['id']}").json()
    assert e["status"] == "acknowledged" and e["reviewed_by"].startswith("phone")
    assert anon.post(f"/api/actions/{actions.make_token('ack', 999999)}", headers=CSRF).status_code == 404
    expired = actions.make_token("ack", ev["id"], now=time.time() - 3 * 3600)
    assert anon.post(f"/api/actions/{expired}", headers=CSRF).status_code == 410


def test_mute_button_silences_only_notifications(anon, client, camera, monkeypatch):
    from app.routes import internal
    sent = []
    monkeypatch.setattr(internal.notifier, "notify", lambda event, cfg: sent.append(event["id"]))
    new_event(client, camera, "before")
    assert len(sent) == 1
    r = anon.post(f"/api/actions/{actions.make_token('mute', camera['id'])}", headers=CSRF)
    assert r.status_code == 200 and "muted" in r.json()["message"]
    assert client.get(f"/api/cameras/{camera['id']}").json()["notify_muted_until"]
    ev = new_event(client, camera, "during")
    assert len(sent) == 1 and ev["id"]  # still recorded, just no push
    actions_log = [a["action"] for a in client.get("/api/audit").json()["items"]]
    assert "camera.mute" in actions_log


def test_dashboard_mute_and_unmute(operator, client, camera):
    r = operator.put(f"/api/cameras/{camera['id']}/mute", json={"minutes": 30})
    assert r.status_code == 200 and r.json()["notify_muted_until"]
    r = operator.put(f"/api/cameras/{camera['id']}/mute", json={"minutes": 0})
    assert r.json()["notify_muted_until"] is None
    assert operator.put(f"/api/cameras/{camera['id']}/mute", json={"minutes": 5000}).status_code == 422


def test_notification_has_buttons_only_with_a_dashboard_link():
    event = {"id": 7, "camera_id": 3, "event_type": "zone_entry", "severity": "high", "camera_name": "Door"}
    assert "actions" not in build_message(event, {})
    msg = build_message(event, {"dashboard_url": "https://box.ts.net/"})
    labels = [a["label"] for a in msg["actions"]]
    assert labels == ["Acknowledge", "Mute camera 1 h"]
    assert msg["actions"][0]["url"].startswith("https://box.ts.net/api/actions/")
    assert msg["actions"][0]["headers"] == {"X-Requested-With": "aivision"}
    header = actions.ntfy_actions_header(msg["actions"])
    assert header.count("http, ") == 2 and "headers.X-Requested-With=aivision" in header
    assert "actions" not in build_message({**event, "event_type": "camera_online"}, {"dashboard_url": "https://x"})
