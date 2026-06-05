from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .snapshot import WatchSnapshot


READY_TIMEOUT_FLOOR_SECONDS = 8.0
READY_TIMEOUT_DEBOUNCE_MULTIPLIER = 8.0


class FileEventWatcher:
    def __init__(
        self,
        root: Path,
        take_snapshot: Callable[[], WatchSnapshot],
        apply_changes: Callable[[WatchSnapshot, WatchSnapshot], dict[str, Any]],
        debounce_seconds: float,
        initial_snapshot: WatchSnapshot | None = None,
    ) -> None:
        self.root = root
        self.take_snapshot = take_snapshot
        self.apply_changes = apply_changes
        self.debounce_seconds = debounce_seconds
        self._initial_snapshot = initial_snapshot
        self._observer = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._changed = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._snapshot: WatchSnapshot | None = None
        self.ready_timeout_seconds = max(
            READY_TIMEOUT_FLOOR_SECONDS,
            debounce_seconds * READY_TIMEOUT_DEBOUNCE_MULTIPLIER,
        )
        self.last_update_at: float | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.change_count = 0
        self.ready = False

    def start(self, wait_ready: bool = True) -> None:
        if self.is_running():
            return
        try:
            self.ready = False
            self.last_error = None
            self._snapshot = self._initial_snapshot
            self._stop.clear()
            self._changed.clear()
            self._ready.clear()
            observer = Observer()
            observer.schedule(_ChangeHandler(self._changed), str(self.root), recursive=True)
            observer.start()
            self._observer = observer
            self._worker = threading.Thread(target=self._run, name="auto-index-watcher", daemon=True)
            self._worker.start()
            self._changed.set()
            if wait_ready and not self._ready.wait(timeout=self.ready_timeout_seconds):
                self.last_error = f"watcher did not become ready within {self.ready_timeout_seconds:g} seconds"
                raise TimeoutError(self.last_error)
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        self._stop.set()
        self._changed.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5.0)
        if self._worker:
            self._worker.join(timeout=5.0)
        self._observer = None
        self._worker = None
        self.ready = False

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.is_running(),
            "ready": self.ready,
            "mode": "filesystem-events",
            "debounce_seconds": self.debounce_seconds,
            "change_count": self.change_count,
            "last_update_at": self.last_update_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._changed.wait(timeout=0.5):
                continue
            self._changed.clear()
            if self._stop.wait(self.debounce_seconds):
                break
            self._apply_snapshot_change()

    def _apply_snapshot_change(self) -> None:
        with self._lock:
            try:
                current = self._settled_snapshot()
                if current == self._snapshot:
                    self.ready = True
                    return
                previous = self._snapshot
                self.change_count += 1
                if previous is not None:
                    self.last_result = self.apply_changes(previous, current)
                self._snapshot = current
                self.last_update_at = time.time()
                self.last_error = None
                self.ready = True
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                self._ready.set()

    def _settled_snapshot(self) -> WatchSnapshot:
        current = self.take_snapshot()
        for _ in range(3):
            if self._stop.wait(self.debounce_seconds):
                return current
            self._changed.clear()
            latest = self.take_snapshot()
            if latest == current:
                return latest
            current = latest
        return current


class _ChangeHandler(FileSystemEventHandler):
    def __init__(self, changed: threading.Event) -> None:
        self.changed = changed

    def on_any_event(self, event: FileSystemEvent) -> None:
        _ = event
        self.changed.set()
