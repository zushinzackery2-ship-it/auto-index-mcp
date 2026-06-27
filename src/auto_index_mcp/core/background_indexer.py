from __future__ import annotations

import threading
import time
from typing import Any, Callable

# Rebuild phases reported by the worker via set_phase(). They mirror the stages
# of _rebuild_now so a polling caller can see where a long build currently sits.
PHASE_IDLE = "idle"
PHASE_SCANNING = "scanning"
PHASE_ANALYZING = "analyzing"
PHASE_WRITING = "writing"
PHASE_EMBEDDING = "embedding"
PHASE_DONE = "done"

# Lifecycle states of the background runner itself.
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"


class BackgroundIndexer:
    """Runs a single full-tree rebuild on a daemon thread.

    The MCP request thread dispatches the heavy scan/analyze/write/embed work
    here and returns immediately, so large projects no longer block enable past
    the host timeout. The worker callable receives this indexer back so it can
    report progress through set_phase(); its return value is stored as
    last_result and, on success, handed to the optional on_done callback (used
    to start the filesystem watcher once the index is actually ready).
    """

    def __init__(
        self,
        work: Callable[["BackgroundIndexer"], dict[str, Any]],
        on_done: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._work = work
        self._on_done = on_done
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._state = STATE_IDLE
        self._phase = PHASE_IDLE
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._error: str | None = None
        self._last_result: dict[str, Any] | None = None

    def start(self, delay_seconds: float = 0.0) -> None:
        """Spawn the worker thread. Idempotent while a build is already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._state = STATE_RUNNING
            self._phase = PHASE_SCANNING
            self._started_at = time.time()
            self._finished_at = None
            self._error = None
            self._last_result = None
            self._done.clear()
            self._thread = threading.Thread(
                target=lambda: self._run_after_delay(delay_seconds),
                name="auto-index-background-indexer",
                daemon=True,
            )
            self._thread.start()

    def _run_after_delay(self, delay_seconds: float) -> None:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        self._run()

    def _run(self) -> None:
        result: dict[str, Any] | None = None
        try:
            result = self._work(self)
            with self._lock:
                self._last_result = result
                self._state = STATE_DONE
                self._phase = PHASE_DONE
                self._finished_at = time.time()
        except Exception as exc:  # noqa: BLE001 - surfaced via status().error
            with self._lock:
                self._error = str(exc)
                self._state = STATE_ERROR
                self._finished_at = time.time()
        finally:
            self._done.set()
        # Fire the completion hook outside the lock and only on a clean result,
        # so a failed build never auto-starts a watcher over a half-written index.
        if result is not None and self._on_done is not None:
            try:
                self._on_done(result)
            except Exception:  # noqa: BLE001 - hook failures must not crash the thread
                pass

    def set_phase(self, phase: str) -> None:
        with self._lock:
            if self._state == STATE_RUNNING:
                self._phase = phase

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the worker finishes. Returns False on timeout."""
        return self._done.wait(timeout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            elapsed: float | None = None
            if self._started_at is not None:
                end = self._finished_at if self._finished_at is not None else time.time()
                elapsed = round(end - self._started_at, 3)
            return {
                "state": self._state,
                "phase": self._phase,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "elapsed_seconds": elapsed,
                "error": self._error,
                "last_result": self._last_result,
            }
