"""System watchdog: tells the owner's phone when the system itself needs attention.

Checked every 30 s by the API:
- the vision worker stopped talking to the API (crashed, hung, or the PC is overloaded);
- the disk holding snapshots and clips is nearly full.

Each problem is pushed once when it starts and once when it clears (no repeats while it
lasts), and is listed in Settings -> System health. Camera outages are already reported by
the worker as camera_offline events, so they are not duplicated here.
"""

import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.notifier import notifier

log = logging.getLogger(__name__)

WORKER_DOWN_AFTER = 120.0  # seconds without contact (the worker reports every 5 s)
STARTUP_GRACE = 300.0  # loading the model can take minutes on a busy laptop
DISK_MIN_FREE_BYTES = 2 * 1024**3
DISK_MIN_FREE_FRACTION = 0.05


@dataclass
class Problem:
    key: str
    title: str
    message: str


class Watchdog:
    def __init__(self, started_at: float | None = None):
        self.started_at = time.time() if started_at is None else started_at
        self.active: dict[str, Problem] = {}

    def evaluate(self, now: float, worker_last: datetime | None, disk_free: int, disk_total: int) -> list[Problem]:
        problems = []
        last = worker_last.timestamp() if worker_last else None
        if last is not None and now - last > WORKER_DOWN_AFTER:
            mins = round((now - last) / 60)
            problems.append(Problem("worker_down", "Vision worker stopped",
                                    f"No detection is running: the vision worker hasn't reported for {mins} min."))
        elif last is None and now - self.started_at > STARTUP_GRACE:
            problems.append(Problem("worker_down", "Vision worker not running",
                                    "No detection is running: the vision worker hasn't connected since the API started."))
        if disk_free < DISK_MIN_FREE_BYTES or disk_free < disk_total * DISK_MIN_FREE_FRACTION:
            problems.append(Problem("disk_low", "Disk almost full",
                                    f"Only {disk_free / 1024**3:.1f} GB free. Lower retention in Settings or free up space."))
        return problems

    def update(self, problems: list[Problem]) -> tuple[list[Problem], list[Problem]]:
        """Returns (started, cleared) since the last update."""
        now = {p.key: p for p in problems}
        started = [p for k, p in now.items() if k not in self.active]
        cleared = [p for k, p in self.active.items() if k not in now]
        self.active = now
        return started, cleared

    def check(self, storage: Path, worker_last: datetime | None, cfg: dict) -> None:
        disk = shutil.disk_usage(storage)
        started, cleared = self.update(self.evaluate(time.time(), worker_last, disk.free, disk.total))
        for p in started:
            log.warning("system problem: %s", p.message)
            notifier.notify_system(cfg, f"⚠ {p.title}", p.message, priority=4, tags=["warning"])
        for p in cleared:
            log.info("system problem cleared: %s", p.key)
            notifier.notify_system(cfg, "Back to normal", f"Resolved: {p.title.lower()}.", priority=2,
                                   tags=["white_check_mark"])

    def status(self) -> list[dict]:
        return [{"key": p.key, "title": p.title, "message": p.message} for p in self.active.values()]


watchdog = Watchdog()
