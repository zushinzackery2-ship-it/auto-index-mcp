from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .snapshot import WatchSnapshot


class FileEventWatcher:
    def __init__(
        self,
        root: Path,
        take_snapshot: Callable[[], WatchSnapshot],
        update_snapshot: Callable[[WatchSnapshot, set[Path]], WatchSnapshot],
        apply_changes: Callable[[WatchSnapshot, WatchSnapshot], dict[str, Any]],
        debounce_seconds: float,
        initial_snapshot: WatchSnapshot | None = None,
    ) -> None:
        self.root = root
        self.take_snapshot = take_snapshot
        self.update_snapshot = update_snapshot
        self.apply_changes = apply_changes
        self.debounce_seconds = debounce_seconds
        self._initial_snapshot = initial_snapshot
        self._observer = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._changed = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._changes_lock = threading.Lock()
        self._changed_paths: set[Path] = set()
        self._needs_full_snapshot = False
        self._snapshot: WatchSnapshot | None = None
        self.last_update_at: float | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.change_count = 0
        self.ready = False

    def start(self, wait_ready: bool = False) -> None:
        if self.is_running():
            return
        try:
            self.ready = False
            self.last_error = None
            self._snapshot = self._initial_snapshot
            with self._changes_lock:
                self._changed_paths.clear()
                self._needs_full_snapshot = True
            self._stop.clear()
            self._changed.clear()
            self._ready.clear()
            observer = Observer()
            observer.schedule(_ChangeHandler(self._record_event), str(self.root), recursive=True)
            observer.start()
            self._observer = observer
            self._worker = threading.Thread(target=self._run, name="auto-index-watcher", daemon=True)
            self._worker.start()
            self._changed.set()
            if wait_ready and not self._ready.wait(timeout=5.0):
                self.last_error = "watcher did not become ready within 5 seconds"
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

    def _record_event(self, event: FileSystemEvent) -> None:
        if event.is_directory and event.event_type == "modified":
            return
        paths = [Path(str(event.src_path))]
        dest_path = getattr(event, "dest_path", "")
        if dest_path:
            paths.append(Path(str(dest_path)))
        with self._changes_lock:
            self._changed_paths.update(paths)
        self._changed.set()

    def _pop_pending_changes(self) -> tuple[bool, set[Path]]:
        with self._changes_lock:
            needs_full_snapshot = self._needs_full_snapshot
            self._needs_full_snapshot = False
            paths = set(self._changed_paths)
            self._changed_paths.clear()
        return needs_full_snapshot, paths

    def _apply_snapshot_change(self) -> None:
        with self._lock:
            paths: set[Path] = set()
            try:
                needs_full_snapshot, paths = self._pop_pending_changes()
                if self._snapshot is None or needs_full_snapshot:
                    current = self.take_snapshot()
                elif not paths:
                    self.ready = True
                    return
                else:
                    current = self.update_snapshot(self._snapshot, paths)
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
                self._requeue_after_error(paths)
            finally:
                self._ready.set()

    def _requeue_after_error(self, paths: set[Path]) -> None:
        if self._stop.is_set():
            return
        with self._changes_lock:
            self._needs_full_snapshot = True
            self._changed_paths.update(paths)
        self._changed.set()


class _ChangeHandler(FileSystemEventHandler):
    def __init__(self, on_event: Callable[[FileSystemEvent], None]) -> None:
        self.on_event = on_event

    def on_any_event(self, event: FileSystemEvent) -> None:
        self.on_event(event)
