from __future__ import annotations

import threading
import time
import logging
import uuid
from typing import Any, Callable

from .diagnostics import diagnostic_scope

# Rebuild phases reported by the worker via set_phase(). They mirror the stages
# of _rebuild_now so a polling caller can see where a long build currently sits.
PHASE_IDLE = "idle"
logger = logging.getLogger(__name__)
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
        *,
        operation: str = "build",
        root: object = None,
        index_root: object = None,
    ) -> None:
        self._work = work
        self._on_done = on_done
        self.operation = operation
        self.operation_id = uuid.uuid4().hex[:12]
        self.root = str(root) if root is not None else None
        self.index_root = index_root
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        self.cancelled = threading.Event()
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
            self.cancelled.clear()
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
            self.cancelled.wait(delay_seconds)
        with diagnostic_scope(self.index_root, self.operation_id):
            self._run()

    def _run(self) -> None:
        result: dict[str, Any] | None = None
        try:
            self.check_cancelled()
            logger.info("operation=%s id=%s root=%s started", self.operation, self.operation_id, self.root)
            result = self._work(self)
            self.check_cancelled()
            with self._lock:
                self._last_result = result
                self._state = STATE_DONE
                self._phase = PHASE_DONE
                self._finished_at = time.time()
            if self._on_done is not None:
                self._on_done(result)
            logger.info("operation=%s id=%s root=%s completed status=%s elapsed=%.3fs",
                        self.operation, self.operation_id, self.root, result.get("status"),
                        time.time() - self._started_at)
        except InterruptedError:
            with self._lock:
                self._state = "cancelled"
                self._finished_at = time.time()
            logger.info("operation=%s id=%s root=%s cancelled", self.operation, self.operation_id, self.root)
        except Exception as exc:  # noqa: BLE001 - surfaced via status().error
            logger.exception("operation=%s id=%s root=%s failed", self.operation, self.operation_id, self.root)
            with self._lock:
                self._error = str(exc)
                self._state = STATE_ERROR
                self._finished_at = time.time()
        finally:
            with self._lock:
                self._finished_at = time.time()
            self._done.set()

    def set_phase(self, phase: str) -> None:
        with self._lock:
            if self._state == STATE_RUNNING:
                if self._phase != phase:
                    self._phase = phase
                    logger.info("operation=%s id=%s phase=%s", self.operation, self.operation_id, phase)

    def check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise InterruptedError("background work cancelled")

    def is_running(self) -> bool:
        with self._lock:
            return self._state == STATE_RUNNING

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for work and its completion callback. Returns False on timeout."""
        return self._done.wait(timeout)

    def join(self, timeout: float | None = None) -> bool:
        """Join the actual worker before releasing its lifecycle resources."""
        thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout)
        return not thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            elapsed: float | None = None
            if self._started_at is not None:
                end = self._finished_at if self._finished_at is not None else time.time()
                elapsed = round(end - self._started_at, 3)
            return {
                "operation_id": self.operation_id,
                "operation": self.operation,
                "state": self._state,
                "phase": self._phase,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "elapsed_seconds": elapsed,
                "error": self._error,
                "last_result": self._last_result,
            }

    def timer(self) -> dict[str, Any]:
        """Normalized build-timer view: state plus real-time elapsed seconds.

        ``elapsed_seconds`` ticks live while the build runs (recomputed against
        the wall clock on every call) and freezes to the total duration once the
        build finishes, so a polling caller always sees current build time.
        """
        snap = self.status()
        return {
            "state": snap["state"],
            "phase": snap["phase"],
            "running": snap["state"] == STATE_RUNNING,
            "elapsed_seconds": snap["elapsed_seconds"],
            "started_at": snap["started_at"],
            "finished_at": snap["finished_at"],
        }


def idle_timer() -> dict[str, Any]:
    """Timer view for a build that has never started in this session."""
    return {
        "state": STATE_IDLE,
        "phase": PHASE_IDLE,
        "running": False,
        "elapsed_seconds": None,
        "started_at": None,
        "finished_at": None,
    }


def timer_or_idle(
    background: "BackgroundIndexer | None",
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Live timer for ``background``; ``fallback`` (e.g. a recorded synchronous
    build) or an idle view when no background runner ever started."""
    if background is not None:
        view = background.timer()
        if view["started_at"] is not None:
            return view
    return dict(fallback) if fallback is not None else idle_timer()
