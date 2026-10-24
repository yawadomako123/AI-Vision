"""Copy all data from PostgreSQL into a new SQLite database file (to run without Docker).

Run from services/backend with the API and worker STOPPED (so nothing changes mid-copy):
    uv run python ../../scripts/migrate-to-sqlite.py                 # -> storage/aivision.db
    uv run python ../../scripts/migrate-to-sqlite.py --switch        # ...and point .env at it

It creates the SQLite schema with the normal migrations, copies every table in dependency order,
checks the row counts match, and with --switch sets DATABASE_URL in .env (the old line is kept
as a comment). PostgreSQL is left untouched, so you can switch back by restoring that line.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "services" / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, insert, select  # noqa: E402

from app.config import REPO_ROOT, settings  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.models import Base  # noqa: E402

CHUNK = 500


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--to", default="storage/aivision.db", help="SQLite file (relative to the project folder)")
    parser.add_argument("--switch", action="store_true", help="set DATABASE_URL in .env to the new database")
    args = parser.parse_args()

    source_url = settings.db_url
    if source_url.startswith("sqlite"):
        print("DATABASE_URL already points at SQLite; nothing to migrate.")
        return 1
    target = (REPO_ROOT / args.to) if not Path(args.to).is_absolute() else Path(args.to)
    if target.exists():
        print(f"{target} already exists. Move it away first (it's never overwritten).")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target_url = f"sqlite:///{target.as_posix()}"

    print(f"1/3 Creating the SQLite schema in {target}")
    env = {**os.environ, "DATABASE_URL": target_url}
    r = subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        return 1

    src, dst = make_engine(source_url), make_engine(target_url)
    print("2/3 Copying tables")
    counts = {}
    with src.connect() as s, dst.begin() as d:
        for table in Base.metadata.sorted_tables:  # parents before children (foreign keys)
            n = 0
            rows = s.execution_options(yield_per=CHUNK).execute(select(table)).mappings()
            batch = []
            for row in rows:
                batch.append(dict(row))
                if len(batch) >= CHUNK:
                    d.execute(insert(table), batch)
                    n += len(batch)
                    batch = []
            if batch:
                d.execute(insert(table), batch)
                n += len(batch)
            counts[table.name] = n
            print(f"    {table.name:18s} {n:7d} rows")

    print("3/3 Checking")
    ok = True
    with src.connect() as s, dst.connect() as d:
        for table in Base.metadata.sorted_tables:
            a = s.execute(select(func.count()).select_from(table)).scalar_one()
            b = d.execute(select(func.count()).select_from(table)).scalar_one()
            if a != b:
                ok = False
                print(f"    MISMATCH {table.name}: PostgreSQL {a}, SQLite {b}")
        integrity = d.exec_driver_sql("PRAGMA integrity_check").scalar_one()
    if not ok or integrity != "ok":
        print(f"    Copy is NOT complete (integrity: {integrity}); .env left unchanged.")
        return 1
    print(f"    All {sum(counts.values())} rows copied; SQLite integrity check: ok")

    rel = target.relative_to(REPO_ROOT).as_posix() if target.is_relative_to(REPO_ROOT) else target.as_posix()
    line = f"DATABASE_URL=sqlite:///{rel}"
    if args.switch:
        env_file = REPO_ROOT / ".env"
        lines = env_file.read_text(encoding="utf-8").splitlines()
        out, done = [], False
        for l in lines:
            if l.strip().startswith("DATABASE_URL="):
                out.append("# previous: " + l)
                if not done:
                    out.append(line)
                    done = True
            else:
                out.append(l)
        if not done:
            out += ["", "# Database: a single SQLite file (no Docker needed). Old PostgreSQL data is untouched.", line]
        env_file.write_bytes(("\n".join(out) + "\n").encode("utf-8"))
        print(f"\n.env now has {line}. Restart the API (supervisor) to use it.")
    else:
        print(f"\nTo use it, put this in .env and restart the API:\n    {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
