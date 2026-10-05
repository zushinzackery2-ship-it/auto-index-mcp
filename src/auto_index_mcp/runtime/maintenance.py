"""Exclusive maintenance uses the same stable files as every writer."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from .leases import BuildLock
from .locking.session import MAINTENANCE_LOCK, SESSION_LOCK
from ..cache.locking import lock_directory
from .control.contracts import remaining

LEASE_NAMES = (
    SESSION_LOCK,
    "watcher.lock", "index.build.lock", "embeddings.build.lock",
    "index.schema.lock", "embeddings.schema.lock",
)
logger = logging.getLogger(__name__)


class MaintenanceLease:
    def __init__(self, directory: Path, operation: str = "maintenance", session=None,
                 coordination_directory: Path | None = None, deadline=None) -> None:
        self.directory = Path(directory).resolve()
        self.operation = operation
        self.operation_id = uuid.uuid4().hex[:12]
        self.session = session
        self.deadline = deadline
        self._resume_session = False
        coordination = coordination_directory or lock_directory(self.directory)
        self.gate = BuildLock(coordination / MAINTENANCE_LOCK, resolve_cache=False)
        self.locks = [BuildLock(coordination / name, resolve_cache=False) for name in LEASE_NAMES]
        if coordination != self.directory:
            self.locks.extend(BuildLock(self.directory / name, resolve_cache=False) for name in LEASE_NAMES
                              if (self.directory / name).exists())

    def __enter__(self):
        try:
            if not self._acquire(self.gate):
                raise TimeoutError(f"active maintenance owns {self.directory}")
            if self.session is not None and self.session.held:
                self.session.release()
                self._resume_session = True
            for lock in self.locks:
                if not self._acquire(lock):
                    raise TimeoutError(f"active session or writer owns {lock.path.name}: {lock.state_info()}")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        logger.info("operation=%s id=%s pid=%s root=%s phase=leased", self.operation,
                    self.operation_id, os.getpid(), self.directory)
        return self

    def _acquire(self, lock):
        return lock.try_acquire() if self.deadline is None else lock.acquire(remaining(self.deadline))

    def __exit__(self, exc_type, exc, traceback) -> None:
        for lock in reversed(self.locks):
            lock.release()
        try:
            if self._resume_session:
                self.session.resume()
                self._resume_session = False
        finally:
            self.gate.release()
        logger.info("operation=%s id=%s pid=%s root=%s phase=released error=%s", self.operation,
                    self.operation_id, os.getpid(), self.directory, exc)
