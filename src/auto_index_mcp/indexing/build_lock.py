from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _LockState:
    token: str
    mtime_ns: int
    age_seconds: float


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
        self._token = f"{os.getpid()}:{time.time_ns()}:{id(self)}"
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def acquire(self, wait_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            if self._try_create():
                self._held = True
                self._start_heartbeat()
                return True
            self._reclaim_if_stale()
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.poll_seconds)

    def release(self) -> None:
        self._stop_heartbeat()
        if not self._held:
            return
        self._held = False
        if not self._owns_lock():
            return
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
            os.write(fd, f"{self._token}\n{time.time()}".encode("ascii", errors="ignore"))
        finally:
            os.close(fd)
        return True

    def _start_heartbeat(self) -> None:
        self._heartbeat_stop.clear()
        interval = max(self.poll_seconds, min(30.0, self.stale_seconds / 4.0))
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat,
            args=(interval,),
            name="auto-index-build-lock-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0)
            self._heartbeat_thread = None

    def _heartbeat(self, interval: float) -> None:
        while not self._heartbeat_stop.wait(interval):
            if not self._owns_lock():
                return
            try:
                now = time.time()
                os.utime(self.path, (now, now))
            except OSError:
                return

    def _reclaim_if_stale(self) -> None:
        state = self._lock_state()
        if state is None or state.age_seconds <= self.stale_seconds:
            return
        latest = self._lock_state()
        if latest is None or latest.token != state.token or latest.mtime_ns != state.mtime_ns:
            return
        try:
            self.path.unlink()
        except OSError:
            pass

    def _lock_state(self) -> _LockState | None:
        try:
            stat = self.path.stat()
            token = self.path.read_text(encoding="ascii", errors="ignore").splitlines()[0]
        except (OSError, IndexError):
            return None
        return _LockState(token=token, mtime_ns=stat.st_mtime_ns, age_seconds=time.time() - stat.st_mtime)

    def _owns_lock(self) -> bool:
        state = self._lock_state()
        return state is not None and state.token == self._token
