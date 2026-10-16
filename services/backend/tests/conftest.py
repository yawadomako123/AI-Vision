import os

from cryptography.fernet import Fernet

# Must be set before app modules are imported (settings are read at import time).
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["CAMERA_SECRET_KEY"] = Fernet.generate_key().decode()
os.environ["SERVICE_TOKEN"] = "test-token"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

WORKER = {"X-Service-Token": "test-token"}


@pytest.fixture
def client(tmp_path):
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def camera(client):
    r = client.post("/api/cameras", json={"name": "Front door", "stream_url": "rtsp://admin:s3cret@10.0.0.5:554/s1"})
    assert r.status_code == 201
    return r.json()
