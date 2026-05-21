from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from .scanner import SourceScanner


class PollingWatcher:
    def __init__(self, root: Path, rebuild: Callable[[], dict], interval_seconds: float = 2.0) -> None:
        self.root = root
        self.rebuild = rebuild
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, tuple[int, int]] = {}
        self.last_rebuild_at: float | None = None
        self.last_error: str | None = None
        self.change_count = 0

    def start(self) -> None:
        if self.is_running():
            return
        self._snapshot = self._take_snapshot()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="auto-index-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "interval_seconds": self.interval_seconds,
            "change_count": self.change_count,
            "last_rebuild_at": self.last_rebuild_at,
            "last_error": self.last_error,
        }

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            current = self._take_snapshot()
            if current == self._snapshot:
                continue
            self._snapshot = current
            self.change_count += 1
            try:
                self.rebuild()
                self.last_rebuild_at = time.time()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)

    def _take_snapshot(self) -> dict[str, tuple[int, int]]:
        scan = SourceScanner(str(self.root)).scan()
        snapshot: dict[str, tuple[int, int]] = {}
        for record in scan.records:
            snapshot[record.path] = (record.size, record.mtime_ns)
        return snapshot
