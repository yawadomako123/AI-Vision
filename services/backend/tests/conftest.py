import os
import tempfile

from cryptography.fernet import Fernet

# Must be set before app modules are imported (settings are read at import time).
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["CAMERA_SECRET_KEY"] = Fernet.generate_key().decode()
os.environ["SERVICE_TOKEN"] = "test-token"
os.environ["RUN_RETENTION"] = "false"
os.environ["EVENT_STORAGE_PATH"] = tempfile.mkdtemp(prefix="aiv-test-media-")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.auth import throttle  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

WORKER = {"X-Service-Token": "test-token"}
CSRF = {"X-Requested-With": "aivision"}
ADMIN_PASSWORD = "admin-password-123"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    throttle._failures.clear()
    yield Session
    app.dependency_overrides.clear()


def make_client() -> TestClient:
    c = TestClient(app)
    c.headers.update(CSRF)
    return c


@pytest.fixture
def anon(db_session):
    with make_client() as c:
        yield c


@pytest.fixture
def client(db_session):
    """Logged-in admin (created through first-run setup)."""
    with make_client() as c:
        r = c.post("/api/auth/setup", json={"username": "admin", "password": ADMIN_PASSWORD})
        assert r.status_code == 201, r.text
        yield c


@pytest.fixture
def operator(client):
    r = client.post("/api/users", json={"username": "olivia", "password": "operator-pass-1", "role": "operator"})
    assert r.status_code == 201, r.text
    with make_client() as c:
        assert c.post("/api/auth/login", json={"username": "olivia", "password": "operator-pass-1"}).status_code == 200
        yield c


@pytest.fixture
def camera(client):
    r = client.post("/api/cameras", json={"name": "Front door", "stream_url": "rtsp://admin:s3cret@10.0.0.5:554/s1"})
    assert r.status_code == 201
    return r.json()
