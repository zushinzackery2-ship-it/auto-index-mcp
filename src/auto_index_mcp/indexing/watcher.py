from __future__ import annotations

import threading
import time
from typing import Callable, Any

from .snapshot import WatchSnapshot


class PollingWatcher:
    def __init__(
        self,
        take_snapshot: Callable[[], WatchSnapshot],
        apply_changes: Callable[[WatchSnapshot, WatchSnapshot], dict[str, Any]],
        interval_seconds: float = 2.0,
    ) -> None:
        self.take_snapshot = take_snapshot
        self.apply_changes = apply_changes
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: WatchSnapshot | None = None
        self.last_update_at: float | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.change_count = 0

    def start(self) -> None:
        if self.is_running():
            return
        self._snapshot = self.take_snapshot()
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
            "last_update_at": self.last_update_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            current = self.take_snapshot()
            if current == self._snapshot:
                continue
            previous = self._snapshot
            self.change_count += 1
            try:
                if previous is not None:
                    self.last_result = self.apply_changes(previous, current)
                self._snapshot = self.take_snapshot()
                self.last_update_at = time.time()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
