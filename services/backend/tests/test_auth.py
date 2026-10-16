from tests.conftest import ADMIN_PASSWORD, CSRF, make_client


def test_status_and_first_run_setup(anon):
    assert anon.get("/api/auth/status").json() == {"setup_required": True, "user": None}
    r = anon.post("/api/auth/setup", json={"username": "boss", "password": "long-enough-pw"})
    assert r.status_code == 201 and r.json()["role"] == "admin"
    s = anon.get("/api/auth/status").json()
    assert s["setup_required"] is False and s["user"]["username"] == "boss"


def test_setup_only_once(client, anon):
    r = anon.post("/api/auth/setup", json={"username": "intruder", "password": "long-enough-pw"})
    assert r.status_code == 409


def test_setup_rejects_short_password(anon):
    assert anon.post("/api/auth/setup", json={"username": "boss", "password": "short"}).status_code == 422


def test_everything_requires_login(anon):
    for path in ("/api/cameras", "/api/events", "/api/analytics/summary", "/api/users", "/api/settings",
                 "/api/system", "/media/events/x.jpg", "/api/events/export.csv"):
        assert anon.get(path).status_code == 401, path


def test_login_logout(client, anon):
    assert anon.post("/api/auth/login", json={"username": "admin", "password": "nope-nope-nope"}).status_code == 401
    r = anon.post("/api/auth/login", json={"username": "ADMIN", "password": ADMIN_PASSWORD})  # case-insensitive
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert anon.get("/api/auth/me").json()["username"] == "admin"
    assert anon.post("/api/auth/logout").status_code == 204
    assert anon.get("/api/auth/me").status_code == 401


def test_cookie_is_secure_behind_https_proxy(client, anon):
    # http (local): not Secure, so it still works on http://127.0.0.1
    r = anon.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert "secure" not in r.headers["set-cookie"].lower()
    # https (e.g. via `tailscale serve`): Secure
    with make_client() as c:
        c.base_url = "https://testserver"
        r = c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        assert "secure" in r.headers["set-cookie"].lower()


def test_csrf_header_required_for_changes(client):
    without = make_client()
    without.cookies = client.cookies
    del without.headers["X-Requested-With"]
    assert without.get("/api/cameras").status_code == 200  # reads are fine
    r = without.post("/api/cameras", json={"name": "x", "stream_url": "rtsp://h/s"})
    assert r.status_code == 403
    assert without.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}).status_code == 403


def test_login_throttle(client, anon):
    for _ in range(5):
        anon.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    r = anon.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 429


def test_operator_cannot_admin(operator, camera):
    assert operator.get("/api/cameras").status_code == 200
    assert operator.post("/api/cameras", json={"name": "x", "stream_url": "rtsp://h/s"}).status_code == 403
    assert operator.patch(f"/api/cameras/{camera['id']}", json={"enabled": False}).status_code == 403
    assert operator.delete(f"/api/cameras/{camera['id']}").status_code == 403
    assert operator.get("/api/users").status_code == 403
    assert operator.get("/api/audit").status_code == 403
    assert operator.patch("/api/settings", json={"event_retention_days": 1}).status_code == 403


def test_deactivated_user_is_logged_out(client, operator):
    users = {u["username"]: u for u in client.get("/api/users").json()}
    client.patch(f"/api/users/{users['olivia']['id']}", json={"active": False})
    assert operator.get("/api/cameras").status_code == 401
    with make_client() as c:
        assert c.post("/api/auth/login", json={"username": "olivia", "password": "operator-pass-1"}).status_code == 401


def test_cannot_remove_last_admin(client):
    me = client.get("/api/auth/me").json()
    r = client.patch(f"/api/users/{me['id']}", json={"role": "operator"})
    assert r.status_code == 400
    assert client.patch(f"/api/users/{me['id']}", json={"active": False}).status_code == 400


def test_duplicate_username(client):
    client.post("/api/users", json={"username": "sam", "password": "password-123", "role": "operator"})
    r = client.post("/api/users", json={"username": "SAM", "password": "password-123", "role": "operator"})
    assert r.status_code == 409


def test_change_password(client):
    r = client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "new-password-123"})
    assert r.status_code == 400
    r = client.post("/api/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "new-password-123"})
    assert r.status_code == 204
    assert client.get("/api/auth/me").status_code == 200  # this browser stays signed in
    with make_client() as c:
        assert c.post("/api/auth/login", json={"username": "admin", "password": "new-password-123"}).status_code == 200


def test_event_reviewer_comes_from_session(operator, camera):
    from tests.test_api import event_payload
    from tests.conftest import WORKER

    e = operator.post("/api/internal/events", headers=WORKER, json=event_payload(camera["id"])).json()
    r = operator.patch(f"/api/events/{e['id']}", json={"status": "acknowledged", "reviewed_by": "someone-else"})
    assert r.json()["reviewed_by"] == "olivia"


def test_audit_log_records_actions(client, camera):
    client.patch(f"/api/cameras/{camera['id']}", json={"stream_url": "rtsp://u:topsecret@10.0.0.9/s"})
    items = client.get("/api/audit").json()["items"]
    actions = [i["action"] for i in items]
    assert {"setup", "camera.create", "camera.update"} <= set(actions)
    assert "topsecret" not in str(items)  # camera passwords never land in the audit log


def test_websocket_requires_login(anon, client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with anon.websocket_connect("/api/ws") as ws:
            ws.receive_text()
    assert exc.value.code == 4401
    with client.websocket_connect("/api/ws"):
        pass


def test_media_requires_login_and_blocks_traversal(client, anon):
    from app.main import MEDIA_ROOT

    (MEDIA_ROOT / "2026" / "10" / "16").mkdir(parents=True, exist_ok=True)
    (MEDIA_ROOT / "2026" / "10" / "16" / "snap.jpg").write_bytes(b"\xff\xd8jpeg")
    assert anon.get("/media/events/2026/10/16/snap.jpg").status_code == 401
    assert client.get("/media/events/2026/10/16/snap.jpg").content == b"\xff\xd8jpeg"
    assert client.get("/media/events/..%2F..%2Fsecret.txt").status_code == 404
    assert CSRF  # keep import used
