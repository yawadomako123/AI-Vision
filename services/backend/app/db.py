from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    return create_engine(url, pool_pre_ping=True)


def _sqlite_pragmas(conn, _record) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")  # cascading deletes (camera -> events, sightings...)
    cur.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
    cur.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, much faster
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


engine = make_engine(settings.db_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
