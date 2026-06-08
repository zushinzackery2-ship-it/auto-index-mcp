from __future__ import annotations

import os
import time
from pathlib import Path


class BuildLock:
    """Best-effort cross-process advisory lock for full index rebuilds.

    Several MCP processes (one per agent) can point at the same project
    directory and try to build its shared index at the same time. This
    serialises full rebuilds through an ``O_EXCL`` lock file so the heavy scan
    happens once and concurrent SQLite writers do not pile up against the busy
    timeout.

    A lock left behind by a crashed process is reclaimed once it is older than
    ``stale_seconds``. A contended acquire that exceeds ``wait_seconds`` returns
    ``False`` so callers can avoid starting a duplicate full scan.
    """

    def __init__(self, path: Path, stale_seconds: float = 120.0, poll_seconds: float = 0.05) -> None:
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.poll_seconds = poll_seconds
        self._held = False

    def acquire(self, wait_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            if self._try_create():
                self._held = True
                return True
            self._reclaim_if_stale()
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.poll_seconds)

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            self.path.unlink()
        except OSError:
            pass

    @property
    def held(self) -> bool:
        return self._held

    def _try_create(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except (FileExistsError, OSError):
            return False
        try:
            os.write(fd, f"{os.getpid()}\n{time.time()}".encode("ascii", errors="ignore"))
        finally:
            os.close(fd)
        return True

    def _reclaim_if_stale(self) -> None:
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return
        if age > self.stale_seconds:
            try:
                self.path.unlink()
            except OSError:
                pass
