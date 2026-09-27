"""Exclusive maintenance uses the same stable files as every writer."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from .leases import BuildLock

LEASE_NAMES = (
    "watcher.lock", "index.build.lock", "embeddings.build.lock",
    "index.schema.lock", "embeddings.schema.lock",
)
logger = logging.getLogger(__name__)


class MaintenanceLease:
    def __init__(self, directory: Path, operation: str = "maintenance") -> None:
        self.directory = Path(directory).resolve()
        self.operation = operation
        self.operation_id = uuid.uuid4().hex[:12]
        self.locks = [BuildLock(self.directory / name) for name in LEASE_NAMES]

    def __enter__(self):
        try:
            for lock in self.locks:
                if not lock.try_acquire():
                    raise TimeoutError(f"active writer owns {lock.path.name}: {lock.state_info()}")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        logger.info("operation=%s id=%s pid=%s root=%s phase=leased", self.operation,
                    self.operation_id, os.getpid(), self.directory)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        for lock in reversed(self.locks):
            lock.release()
        logger.info("operation=%s id=%s pid=%s root=%s phase=released error=%s", self.operation,
                    self.operation_id, os.getpid(), self.directory, exc)
