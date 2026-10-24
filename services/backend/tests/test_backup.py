import sqlite3
from contextlib import closing

import pytest

from app import backup


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    db = tmp_path / "live.db"
    with closing(sqlite3.connect(db)) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("create table cameras (id integer primary key, name text)")
        c.executemany("insert into cameras (name) values (?)", [("Gate",), ("Door",)])
        c.commit()
    monkeypatch.setattr(backup, "db_path", lambda: db)
    monkeypatch.setattr(backup, "_env_value", lambda key: "")
    monkeypatch.delenv("BACKUP_COPY_DIR", raising=False)
    return db


def rows(path):
    with closing(sqlite3.connect(path)) as c:
        return [r[0] for r in c.execute("select name from cameras order by id")]


def test_backup_prunes_and_restore_round_trips(live_db, tmp_path):
    out = tmp_path / "backups"
    for _ in range(3):
        b = backup.backup(out, keep=2)
    assert len(list(out.glob("aivision-*.sqlite"))) <= 2
    restored = backup.restore(b, tmp_path / "restored.db")
    assert rows(restored) == ["Gate", "Door"]
    # Restoring again over an existing file works on Windows too (files are closed properly).
    restored.unlink()
    assert rows(backup.restore(b, tmp_path / "restored.db")) == ["Gate", "Door"]


def test_corrupt_backup_is_refused(live_db, tmp_path):
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    with pytest.raises((SystemExit, sqlite3.DatabaseError)):
        backup.restore(bad, tmp_path / "x.db")
    assert not (tmp_path / "x.db").exists()


def test_copy_to_cloud_folder(live_db, tmp_path, monkeypatch):
    cloud = tmp_path / "OneDrive" / "AI Vision backups"
    monkeypatch.setenv("BACKUP_COPY_DIR", str(cloud))
    backup.backup(tmp_path / "backups", keep=14)
    assert len(list(cloud.glob("aivision-*.sqlite"))) == 1
