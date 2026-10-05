"""A mounted project stays protected even when it owns no writer lease."""
from __future__ import annotations

import os
from pathlib import Path

from ..leases import BuildLock
from .shared import release_shared_lock, try_shared_lock
from ...cache.locking import lock_directory

SESSION_LOCK = "sessions.lock"
MAINTENANCE_LOCK = "maintenance.lock"


class SessionLease:
    def __init__(self, directory: Path, root=None, control=None) -> None:
        self.directory = Path(directory).resolve()
        self.lock_directory = lock_directory(self.directory)
        self._fd: int | None = None
        self.root, self.control = root, control
        self._mounted = False

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        if self.control is not None:
            self.control.check(self.root)
        if self.held:
            return
        gate = BuildLock(self.lock_directory / MAINTENANCE_LOCK)
        if not gate.acquire(3.0):
            raise TimeoutError(f"cache maintenance is busy: {self.directory}")
        try:
            if lock_directory(self.directory) != self.lock_directory:
                raise TimeoutError(f"cache location changed during attachment: {self.directory}")
            self.resume()
            if self.control is not None:
                try:
                    self.control.check(self.root)
                    self._mounted = True
                    self.control.mount(self.directory, self.root)
                except BaseException:
                    self.release()
                    raise
        finally:
            gate.release()

    def resume(self) -> None:
        """Caller owns the maintenance gate during upgrade/downgrade."""
        if self.held:
            return
        self.lock_directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_directory / SESSION_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if not try_shared_lock(fd):
                raise TimeoutError(f"cache is being maintained: {self.directory}")
            self._fd = fd
        finally:
            if self._fd != fd:
                os.close(fd)

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                release_shared_lock(fd)
            finally:
                os.close(fd)
        if self._mounted:
            self._mounted = False
            self.control.unmount(self.directory)
