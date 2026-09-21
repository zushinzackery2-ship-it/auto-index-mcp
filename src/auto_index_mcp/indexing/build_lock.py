from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from .locking.native import process_alive, try_lock, unlock

logger = logging.getLogger(__name__)


class BuildLock:
    """Kernel-owned lock, automatically released when its process exits.

    The stable file must never be unlinked: contenders must lock the same
    inode. Byte zero is reserved for the Windows byte-range lock; diagnostics
    start at byte one so other processes can read them while it is held.
    Neither timestamps nor PID reuse can revoke a live owner's lock.
    """

    def __init__(self, path: Path, stale_seconds: float = 120.0, poll_seconds: float = 0.05) -> None:
        self.path = Path(path)
        self.poll_seconds = poll_seconds
        self._fd: int | None = None
        self._mutex = threading.Lock()

    def acquire(self, wait_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            with self._mutex:
                if self._fd is not None:
                    return False
                if self._try_acquire():
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(self.poll_seconds, remaining))

    def _try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            legacy = _read_legacy_token(fd)
            if legacy is not None and process_alive(legacy[0]) is not False:
                return False
            if not try_lock(fd):
                return False
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, b"\x00")
            os.lseek(fd, 1, os.SEEK_SET)
            payload = f"{os.getpid()}:{time.time_ns()}\n".encode("ascii")
            os.write(fd, payload)
            os.ftruncate(fd, 1 + len(payload))
            self._fd = fd
            logger.debug("lock acquired path=%s pid=%s", self.path, os.getpid())
            return True
        finally:
            if self._fd != fd:
                os.close(fd)

    def try_acquire(self) -> bool:
        return self.acquire(0.0)

    def release(self) -> None:
        with self._mutex:
            fd = self._fd
            if fd is None:
                return
            self._fd = None
            try:
                os.ftruncate(fd, 1)
                unlock(fd)
            finally:
                os.close(fd)
            logger.debug("lock released path=%s pid=%s", self.path, os.getpid())

    @property
    def held(self) -> bool:
        return self._fd is not None

    def state_info(self) -> dict[str, object] | None:
        try:
            probe = os.open(self.path, os.O_RDWR)
            try:
                if try_lock(probe):
                    unlock(probe)
                    legacy = _read_legacy_token(probe)
                    if legacy is None:
                        return None
                    pid, started = legacy
                    alive = process_alive(pid)
                    if alive is False:
                        return None
                    return dict(
                        holder_pid=pid,
                        holder_alive=alive,
                        lock_age_seconds=max(0.0, round(time.time() - started, 1)),
                        mechanism="legacy-metadata",
                    )
            finally:
                os.close(probe)
            with self.path.open("rb") as stream:
                stream.seek(1)
                token = stream.read(128).decode("ascii").strip()
            pid, started = (int(part) for part in token.split(":", 1))
        except (OSError, ValueError, UnicodeError):
            return None
        return dict(
            holder_pid=pid,
            holder_alive=True,
            lock_age_seconds=max(0.0, round(time.time() - started / 1e9, 1)),
            mechanism="kernel",
        )


def _read_legacy_token(fd: int) -> tuple[int, float] | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, 128)
    except OSError:
        return None
    if not data or data[:1] == b"\x00":
        return None
    try:
        fields = data.decode("ascii").splitlines()[0].split(":")
        pid = int(fields[0])
        started = float(fields[1])
        if started > 10_000_000_000:
            started /= 1e9
        return pid, started
    except (ValueError, UnicodeError, IndexError):
        return None
