"""Backups for the SQLite database (PostgreSQL installs use pg_dump in scripts/backup.ps1).

    uv run python -m app.backup                       # backups/aivision-<time>.sqlite
    uv run python -m app.backup --keep 14             # ...and keep only the newest 14
    uv run python -m app.backup --restore FILE --to storage/restore-test.db   # restore elsewhere to check it
    uv run python -m app.backup --restore FILE        # restore over the live database (stop the API first!)

Uses SQLite's online backup, so the copy is consistent even while the API is writing. A
restore is checked with PRAGMA integrity_check before it replaces anything. Backups are also
copied to BACKUP_COPY_DIR from .env (a OneDrive / Google Drive folder) when set.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app.config import REPO_ROOT, settings


def db_path() -> Path:
    url = settings.db_url
    if not url.startswith("sqlite:///") or url.startswith("sqlite:///:memory:"):
        raise SystemExit("DATABASE_URL isn't a SQLite file; use scripts/backup.ps1 (pg_dump) instead.")
    return Path(url.removeprefix("sqlite:///"))


# "with sqlite3.connect()" only commits; it doesn't close the file, and Windows can't rename or
# delete an open file. closing() makes sure every connection is closed.
def _copy(src: Path, dst: Path) -> None:
    with closing(sqlite3.connect(src)) as s, closing(sqlite3.connect(dst)) as d:
        s.backup(d)
        d.execute("PRAGMA journal_mode=DELETE")  # a single self-contained file
        d.commit()


def _check(path: Path) -> str:
    with closing(sqlite3.connect(path)) as c:
        return c.execute("PRAGMA integrity_check").fetchone()[0]


def backup(out_dir: Path, keep: int = 0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"aivision-{datetime.now():%Y%m%d-%H%M%S}.sqlite"
    _copy(db_path(), dst)
    print(f"Database -> {dst} ({dst.stat().st_size // 1024} KB)")
    if keep > 0:
        for old in sorted(out_dir.glob("aivision-*.sqlite"), reverse=True)[keep:]:
            old.unlink()
            print(f"Removed old backup {old.name}")
    copy_dir = os.environ.get("BACKUP_COPY_DIR") or _env_value("BACKUP_COPY_DIR")
    if copy_dir:
        try:
            Path(copy_dir).mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, copy_dir)
            print(f"Copied -> {copy_dir}")
            if keep > 0:
                for old in sorted(Path(copy_dir).glob("aivision-*.sqlite"), reverse=True)[keep:]:
                    old.unlink()
        except OSError as exc:
            print(f"WARNING: couldn't copy the backup to {copy_dir} ({exc}); the local copy is fine.")
    return dst


def restore(src: Path, to: Path | None = None) -> Path:
    if _check(src) != "ok":
        raise SystemExit(f"{src} failed its integrity check; not restoring it.")
    target = to or db_path()
    target = target if target.is_absolute() else REPO_ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".restoring")
    _copy(src, tmp)
    for suffix in ("-wal", "-shm"):  # stale WAL files of the old database would corrupt the new one
        Path(str(target) + suffix).unlink(missing_ok=True)
    os.replace(tmp, target)
    print(f"Restored {src} -> {target} (integrity: {_check(target)})")
    return target


def _env_value(key: str) -> str:
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--keep", type=int, default=0)
    p.add_argument("--out", type=Path, default=REPO_ROOT / "backups")
    p.add_argument("--restore", type=Path)
    p.add_argument("--to", type=Path)
    a = p.parse_args()
    if a.restore:
        restore(a.restore, a.to)
    else:
        backup(a.out, a.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
